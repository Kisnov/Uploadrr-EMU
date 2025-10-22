from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import psutil

from src.screenshots.generator import ScreenshotGenerator
from src.screenshots.rehoster import RehostManager
from src.screenshots.up2host import ScreenshotUploader
from src.utils.console import log


class ScreenshotManager:
    def __init__(self, config: dict, base_dir: str | Path):
        self.config = config
        self.base_dir = Path(base_dir)

        self.generator = ScreenshotGenerator(config, self.base_dir)
        self.uploader = ScreenshotUploader(config, self.base_dir)
        self.rehoster = RehostManager(config, self.base_dir, self.uploader)

    async def handle_screenshots(self, meta: Dict, tracker_whitelists: Dict[str, List[str]], task_widget=None) -> Dict:
        log.debug("in handle_screenshots")
        try:
            # Handle Music
            if meta.get("is_music"):
                return await self._handle_music(meta, tracker_whitelists)

            # Handle Videos
            return await self._handle_video_content(meta, tracker_whitelists, task_widget)

        except Exception as e:
            log.exception(f"Error in handle_screenshots: {e}")
            return meta

        finally:
            await self._cleanup(meta)
            self._reset_terminal()

    async def _handle_music(self, meta: Dict, tracker_whitelists: Dict[str, List[str]]) -> Dict:
        """Handle music-specific cover and proof uploads"""
        log.debug("Handling music content")
        meta.setdefault("image_list", {"main": [], "episodes": {}, "contact_sheets": {}})
        image_list = meta["image_list"]
        needs_upload = False

        # Check if covers/proofs exist and need upload
        if meta.get("user_cover") and not self._is_valid_url(meta.get("album_cover")):
            needs_upload = True

        if meta.get("back_cover") and not self._is_valid_url(meta.get("album_back")):
            needs_upload = True

        if meta.get("user_proof"):
            needs_upload = True

        if needs_upload and not meta.get("skip_imghost_upload", False):
            log.debug(">>> MUSIC UPLOAD PROCESS")

            music_images = await self._prepare_music_images(meta)

            if music_images:
                meta["image_list"] = await self.uploader.upload_screenshots(
                    meta, music_images, RehostManager._get_viable_hosts(self.config, meta, tracker_whitelists), tracker_whitelists
                )

                for img in meta["image_list"]["main"]:
                    if img.get("local_file", "").startswith("front_cover"):
                        meta["album_cover"] = img.get("img_url")
                    elif img.get("local_file", "").startswith("back_cover"):
                        meta["album_back"] = img.get("img_url")
                    elif img.get("local_file", "").startswith("proof"):
                        if "proof_url" not in meta:
                            meta["proof_url"] = img.get("img_url")

        # Handle rehosting
        if tracker_whitelists and needs_upload:
            whitelist_hosts = set.union(*[set(wl) for wl in tracker_whitelists.values()])

            def non_compliant(section):
                return any(
                    not RehostManager._extract_host(img.get("img_url", ""), whitelist_hosts)
                    for img in section
                    if isinstance(img, dict) and img.get("img_url")
                )

            if non_compliant(image_list["main"]):
                log.debug(">>> REHOSTER CALLED FOR MUSIC")
                await self.rehoster.ensure_compliance(meta, tracker_whitelists)

        return meta

    async def _handle_video_content(self, meta: Dict, tracker_whitelists: Dict[str, List[str]], task_widget=None) -> Dict:
        """Handle existing movie/TV functionality"""
        meta.setdefault("image_list", {"main": [], "episodes": {}, "contact_sheets": {}})
        image_list = meta["image_list"]
        if isinstance(image_list, list):  # backwards compatibility
            image_list = {"main": image_list, "episodes": {}}
            meta["image_list"] = image_list

        screens_main = meta.get("screens", self.config["DEFAULT"].get("screens", 3))
        tmp_dir = self.base_dir / "tmp" / meta["uuid"]

        need_generation = False
        need_upload = False

        # --- Check MAIN --- #
        if self._missing_urls(image_list["main"]):
            need_upload = True
            existing_files = list(tmp_dir.glob(f"{meta['filename']}-*.png"))
            if len(existing_files) < screens_main:
                need_generation = True

        # --- Check Episodes --- #
        if meta.get("tv_pack") == 1:
            per_episode = self.config["SCREENS"].get("per_episode", 3)
            filelist = meta.get("filelist", [])
            for ep_path in filelist:
                if not self.generator._is_video_file(ep_path):
                    continue
                ep_name = self.generator._get_episode_identifier(ep_path)
                ep_screens = image_list["episodes"].get(ep_name, [])

                # Always check if current episode screenshots have valid URLs
                if self._missing_urls(ep_screens):
                    need_upload = True
                    # If we have fewer than required locally, generate more
                    ep_filename = f"{meta['filename']}-{ep_name}"
                    existing_files = list(tmp_dir.glob(f"{ep_filename}-*.png"))
                    if len(existing_files) < per_episode:
                        need_generation = True

        # --- Check Contact Sheet --- #
        contact_cfg = self.config["SCREENS"].get("contact_sheet", {})
        if meta.get("xxx") and not contact_cfg.get("skip", False):
            contact_section = image_list.get("contact_sheets", {})

            if self._missing_urls(contact_section):
                need_upload = True
                contact_file = tmp_dir / "contact_sheet.png"
                if not contact_file.exists():
                    need_generation = True

        log.debug(f"Need Generation = {need_generation} \n Need Upload = {need_upload}")

        if need_upload and not need_generation:
            log.debug("Populating missing local files in image_list without overwriting valid uploads.")

            # --- Populate MAIN if needed --- #
            if self._missing_urls(image_list["main"]):
                existing_files = sorted(tmp_dir.glob(f"{meta['filename']}-*.png"))[:screens_main]

                # Dict of valid existing uploads keyed by local_file
                valid_uploads = {
                    entry["local_file"]: entry
                    for entry in image_list["main"]
                    if isinstance(entry, dict)
                    and "local_file" in entry
                    and {"img_url", "raw_url", "web_url"}.intersection(entry.keys())
                }

                new_main = []
                # Keep all valid uploads, even if local file is missing
                for entry in image_list["main"]:
                    if (
                        isinstance(entry, dict)
                        and "local_file" in entry
                        and {"img_url", "raw_url", "web_url"}.intersection(entry.keys())
                    ):
                        new_main.append(entry)

                # Add new entries for local files that don't have valid uploads
                for f in existing_files:
                    if f.name not in valid_uploads:
                        new_main.append({"local_file": f.name, "ss_time": 0})

                image_list["main"] = new_main[:screens_main]  # Limit to required number

            # --- Populate Episodes if needed --- #
            if meta.get("tv_pack") == 1:
                per_episode = self.config["SCREENS"].get("per_episode", 3)
                filelist = meta.get("filelist", [])
                for ep_path in filelist:
                    if not self.generator._is_video_file(ep_path):
                        continue
                    ep_name = self.generator._get_episode_identifier(ep_path)
                    ep_screens = image_list["episodes"].get(ep_name, [])
                    if self._missing_urls(ep_screens):
                        ep_filename = f"{meta['filename']}-{ep_name}"
                        existing_files = sorted(tmp_dir.glob(f"{ep_filename}-*.png"))[:per_episode]

                        valid_uploads = {
                            entry["local_file"]: entry
                            for entry in ep_screens
                            if isinstance(entry, dict)
                            and "local_file" in entry
                            and {"img_url", "raw_url", "web_url"}.intersection(entry.keys())
                        }

                        new_ep = []
                        # Keep all valid uploads, even if local file is missing
                        for entry in ep_screens:
                            if (
                                isinstance(entry, dict)
                                and "local_file" in entry
                                and {"img_url", "raw_url", "web_url"}.intersection(entry.keys())
                            ):
                                new_ep.append(entry)

                        # Add new entries for local files that don't have valid uploads
                        for f in existing_files:
                            if f.name not in valid_uploads:
                                new_ep.append({"local_file": f.name, "ss_time": 0})

                        image_list["episodes"][ep_name] = new_ep[:per_episode]

            # --- Populate Contact if needed --- #
            contact_cfg = self.config["SCREENS"].get("contact_sheet", {})
            if meta.get("xxx") and not contact_cfg.get("skip", False):
                contact_section = image_list.get("contact_sheets", [])
                if self._missing_urls(contact_section):
                    contact_file = tmp_dir / "contact_sheet.png"
                    # Check if a valid entry already exists
                    valid_contact = [
                        entry
                        for entry in contact_section
                        if isinstance(entry, dict)
                        and "local_file" in entry
                        and entry["local_file"] == "contact_sheet.png"
                        and {"img_url", "raw_url", "web_url"}.intersection(entry.keys())
                    ]
                    if not valid_contact and contact_file.exists() and contact_file.stat().st_size > 1000:
                        # Add only if missing and file exists
                        image_list["contact_sheets"] = [{"local_file": "contact_sheet.png"}]
                    else:
                        # Preserve valid or keep empty if no file
                        image_list["contact_sheets"] = valid_contact

        # --- GENERATE --- #
        if need_generation:
            log.debug(">>> GENERATING SCREENSHOTS")
            meta["image_list"] = await self.generator.generate_screenshots(meta, meta["filename"], meta["uuid"])
            image_list = meta["image_list"]
            need_upload = True  # Generated content always needs upload

        # --- UPLOAD --- #
        if need_upload and not meta.get("skip_imghost_upload", False):
            log.debug(">>> UPLOADER CALLED")
            meta["image_list"] = await self.uploader.upload_screenshots(
                meta, meta["image_list"], RehostManager._get_viable_hosts(self.config, meta, tracker_whitelists), tracker_whitelists
            )
            image_list = meta["image_list"]

        # --- REHOST --- #
        if tracker_whitelists:
            whitelist_hosts = set.union(*[set(wl) for wl in tracker_whitelists.values()])

            def non_compliant(section):
                return any(
                    not RehostManager._extract_host(img.get("img_url", ""), whitelist_hosts)
                    for img in section
                    if isinstance(img, dict) and img.get("img_url")
                )

            if non_compliant(image_list["main"]) or any(non_compliant(lst) for lst in image_list["episodes"].values()):
                log.debug(">>> REHOSTER CALLED")
                await self.rehoster.ensure_compliance(meta, tracker_whitelists)

        return meta

    async def _prepare_music_images(self, meta: Dict) -> Dict:
        """Prepare music images (covers and proofs) for upload"""
        music_images = {"main": [], "episodes": {}, "contact_sheets": []}

        # Add front cover if needed
        if meta.get("user_cover") and Path(meta["user_cover"]).exists() and not self._is_valid_url(meta.get("album_cover")):
            music_images["main"].append({"local_file": meta["user_cover"], "type": "front_cover"})

        # Add back cover if needed
        if meta.get("back_cover") and Path(meta["back_cover"]).exists() and not self._is_valid_url(meta.get("album_back")):
            music_images["main"].append({"local_file": meta["back_cover"], "type": "back_cover"})

        # Add proof if needed
        if meta.get("user_proof") and Path(meta["user_proof"]).exists():
            proof_already_uploaded = any(
                isinstance(img, dict)
                and img.get("local_file", "").startswith("proof")
                and {"img_url", "raw_url", "web_url"}.intersection(img.keys())
                for img in meta.get("image_list", {}).get("main", [])
            )

            if not proof_already_uploaded:
                music_images["main"].append({"local_file": meta["user_proof"], "type": "proof"})

        return music_images

    def _is_valid_url(self, url):
        """Check if a URL is valid (not empty, not None, and looks like a URL)"""
        if not url:
            return False
        if isinstance(url, str):
            return url.startswith(("http://", "https://"))
        return False

    def _missing_urls(self, section):
        if not section:
            return True

        def process_item(item):
            if isinstance(item, str):
                return True
            if isinstance(item, dict):
                required_keys = {"img_url", "raw_url", "web_url"}
                if required_keys.intersection(item.keys()):
                    return False
                return True
            return False

        if isinstance(section, list):
            return any(process_item(item) for item in section)
        elif isinstance(section, dict):
            if len(section) == 1 and "local_file" in section:
                return True
            return any(process_item(value) for value in section.values())
        return False

    async def _cleanup(self, meta: dict) -> None:
        if self.config["SCREENS"].get("cleanup_screens", False):
            tmp_dir = self.base_dir / "tmp" / meta["uuid"]
            for f in tmp_dir.glob("*.png"):
                f.unlink()
        current_process = psutil.Process()
        for child in current_process.children(recursive=True):
            if "ffmpeg" in child.name().lower():
                child.terminate()

    def _reset_terminal(self):
        if os.name == "posix":
            try:
                if not sys.stderr.closed:
                    sys.stderr.flush()
                if hasattr(sys.stdin, "isatty") and sys.stdin.isatty() and not sys.stdin.closed:
                    subprocess.run(["stty", "sane"], check=False)
                    try:
                        import termios

                        termios.tcflush(sys.stdin.fileno(), termios.TCIOFLUSH)
                    except (ImportError, OSError):
                        pass
                        sys.stdout.write("\033[0m")
                        sys.stdout.flush()
                        sys.stdout.write("\033[?25h")
                        sys.stdout.flush()
                os.system("jobs -p | xargs -r kill 2>/dev/null")
            except Exception as e:
                log.debug(f"Error during terminal reset: {e}")

        elif os.name == "nt":
            try:
                import ctypes

                ctypes.windll.kernel32.SetConsoleTextAttribute(ctypes.windll.kernel32.GetStdHandle(-11), 7)

                if sys.stdout.isatty():
                    sys.stdout.write("\033[?25h")
                    sys.stdout.flush()

                if hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
                    try:
                        import msvcrt

                        while msvcrt.kbhit():
                            msvcrt.getch()
                    except ImportError:
                        pass

            except Exception as e:
                log.debug(f"Error during terminal reset: {e}")
