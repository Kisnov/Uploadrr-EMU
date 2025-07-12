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
        try:
            meta.setdefault("image_list", {"main": [], "episodes": {}})
            image_list = meta["image_list"]
            if isinstance(image_list, list):  # backwards compatibility
                image_list = {"main": image_list, "episodes": {}}
            screens_main = meta.get("screens", self.config["DEFAULT"].get("screens", 3))

            tmp_dir = self.base_dir / "tmp" / meta["uuid"]
            contact_cfg = self.config["SCREENS"].get("contact_sheet", {})
            need_contact = bool(meta.get("xxx") and contact_cfg and not (tmp_dir / "contact_sheet.png").exists())
            sufficient = self.generator._is_sufficient(image_list, meta, screens_main)

            # GENERATE
            if not sufficient or need_contact:
                meta["image_list"] = await self.generator.generate_screenshots(meta, meta["filename"], meta["uuid"])
                image_list = meta["image_list"]

            # UPLOAD
            def missing_urls(section):
                return any(not all(img.get(k) for k in ("img_url", "raw_url", "web_url")) for img in section)

            upload_main = missing_urls(image_list["main"])
            upload_eps = any(missing_urls(lst) for lst in image_list["episodes"].values())
            if (upload_main or upload_eps) and not meta.get("skip_imghost_upload", False):
                log.debug(">>> UPLOADER CALLED")
                meta["image_list"] = await self.uploader.upload_screenshots(
                    meta,
                    meta["image_list"],
                    RehostManager._get_viable_hosts(self.config, meta, tracker_whitelists),
                    tracker_whitelists,
                )
                image_list = meta["image_list"]

            # REHOST
            if tracker_whitelists:
                whitelist_hosts = set.union(*[set(wl) for wl in tracker_whitelists.values()])

                def non_compliant(section):
                    return any(not RehostManager._extract_host(img.get("img_url", ""), whitelist_hosts) for img in section)

                if non_compliant(image_list["main"]) or any(non_compliant(lst) for lst in image_list["episodes"].values()):
                    log.debug(">>> REHOSTER CALLED")
                    await self.rehoster.ensure_compliance(meta, tracker_whitelists)

            return meta

        finally:
            await self._cleanup(meta)
            self._reset_terminal()

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
