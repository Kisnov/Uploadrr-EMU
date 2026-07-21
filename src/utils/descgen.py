import json
import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse

import aiofiles
import httpx
import requests

from src.progress import ProgressManager
from src.utils.bbcode import BBCODE

from .console import console, log


@dataclass
class DescControl:
    """Controller for description handling via comment"""

    as_is: bool = False
    include_screens: bool = True
    include_logo: bool = True
    include_trailer: bool = True
    include_signature: bool = True
    modify_img_size: bool = True
    include_disc_info: bool = True

    @staticmethod
    def from_markers(marker_str: str) -> "DescControl":
        control = DescControl()
        if not marker_str.startswith("# USE:"):
            return control

        options = marker_str[len("# USE:") :].strip().split("+")
        main_option = options[0].lower()

        if main_option == "as-is":
            control.as_is = True
            control.include_screens = False
            control.include_logo = False
            control.include_trailer = False
            control.include_signature = False
            control.include_disc_info = False
        elif main_option == "signature-only":
            control.as_is = False
            control.include_screens = False
            control.include_logo = False
            control.include_trailer = False
            control.include_signature = True
            control.include_disc_info = False
        elif main_option == "full":
            control.as_is = False
        else:
            control.as_is = False
            control.include_screens = "screens" in options
            control.include_logo = "logo" in options
            control.include_trailer = "trailer" in options
            control.include_signature = "signature" in options or "sig" in options
            control.include_disc_info = "disc" in options

        if "no-imgsize" in marker_str.lower():
            control.modify_img_size = False

        return control


class DescGenerator:
    """
    Utility for generating descriptions based on metadata.
    Handles various sources like PTP, BLU, templates, NFO, links, and custom files.
    Also handles formatting for specific trackers.
    """

    def __init__(self, config: Dict, base_dir: Path):
        self.config = config
        self.base_dir = base_dir
        self.pm = ProgressManager.get_instance()

    async def read_log_file(self, log_file):
        """Reads a log file, attempting different encodings."""
        encodings = ["utf-16", "utf-8", "latin-1"]

        for encoding in encodings:
            try:
                async with aiofiles.open(log_file, "r", encoding=encoding, errors="ignore" if encoding == "latin-1" else None) as log:
                    return await log.read()
            except (UnicodeDecodeError, UnicodeError):
                continue
            except Exception as e:
                return f"Error reading log file: {str(e)}"

        return "Error: Could not decode file with any supported encoding"

    async def gen_desc(self, meta: Dict) -> Dict:
        desclink = meta.get("desclink", None)
        auto_desc = meta.get("auto_desc", False) or self.config["AUTO"].get("always_aid", False)
        nfo_file = meta.get("nfo_file", None)
        description_path = self.base_dir / "tmp" / f"{meta['uuid']}" / "DESCRIPTION.txt"
        descfile_content = ""
        custom_desc_written = False

        if self.pm:
            self.pm.update("Generating description...")

        description_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(description_path, "w", newline="", encoding="utf-8") as description:
            if (desclink, meta.get("descfile", None), meta["desc"]) == (None, None, None):
                # desc_source = None
                # if meta.get("ptp_manual") is not None:
                #     desc_source.append("PTP")
                # if meta.get("blu_manual") is not None:
                #     desc_source.append("BLU")
                # desc_source = desc_source[0] if len(desc_source) == 1 else None

                # Try PTP description via API
                if (
                    meta.get("ptp") and str(self.config["TRACKERS"].get("PTP", {}).get("useAPI")).lower() == "true"
                    # and desc_source in ["PTP", None]
                ):
                    from src.trackers.PTP import PTP

                    ptp = PTP(config=self.config)
                    ptp_desc = await ptp.get_ptp_description(meta["ptp"], meta["is_disc"])
                    if ptp_desc.strip():
                        await description.write(ptp_desc)
                        await description.write("\n")
                        meta["description"] = "PTP"
                        custom_desc_written = True

                # Try BLU description
                if not custom_desc_written and meta.get("blu_desc", "").strip():
                    trusted_uploaders = self.config["AUTO"].get("trusted_uploader", [])

                    if trusted_uploaders:
                        uploader = meta.get("blu_data").get("uploader")
                        if uploader in trusted_uploaders or uploader == "USE_ALL":
                            use_blu_desc = True
                        else:
                            use_blu_desc = False
                            log.debug(f"BLU uploader '{uploader}' not in trusted list: {trusted_uploaders}")

                        if use_blu_desc:
                            blu_desc = meta["blu_desc"].strip()
                            if blu_desc:
                                await description.write("# USE: as-is")
                                await description.write(blu_desc)
                                meta["description"] = "BLU"
                                custom_desc_written = True

                # Try Jinja2 template
                if meta.get("desc_template"):
                    from jinja2 import Template

                    template_path = Path(meta["base_dir"]) / "data" / "templates" / f"{meta['desc_template']}.txt"
                    if template_path.is_file():
                        with open(template_path, "r", encoding="utf-8") as f:
                            desc_template = Template(f.read())
                            template_desc = desc_template.render(meta)
                            if template_desc.strip():
                                await description.write(template_desc)
                                await description.write("\n")
                                custom_desc_written = True

                # Add NFO file if requested
                if meta["nfo"] and nfo_file:
                    nfo_path = Path(nfo_file)
                    if nfo_path.is_file():
                        async with aiofiles.open(nfo_path, "r", encoding="utf-8") as nfo_f:
                            nfo_content = await nfo_f.read()
                        await description.write("\n[code]")
                        await description.write(nfo_content)
                        await description.write("[/code]\n")
                        meta["description"] = "CUSTOM"
                        custom_desc_written = True

                if desclink:
                    parsed = urllib.parse.urlparse(desclink.replace("/raw/", "/"))
                    split_path = parsed.path.rstrip("/").split("/")
                    if len(split_path) > 1:
                        new_path = "/" + "/".join(split_path[:-1] + ["raw", split_path[-1]])
                    else:
                        new_path = "/raw" + parsed.path

                    raw_url = parsed._replace(path=new_path)
                    raw_str = urllib.parse.urlunparse(raw_url)
                    response = requests.get(raw_str)
                    response.encoding = "utf-8"
                    await description.write(response.text)
                    await description.write("\n")
                    meta["description"] = "CUSTOM"
                    custom_desc_written = True

                # Auto-desc from local file
                if auto_desc:
                    path = Path(meta.get("path", ""))
                    uuid = meta.get("uuid", "")
                    if uuid:
                        uuid = Path(uuid).stem
                    desc_folder = self.config["AUTO"].get("description_folder")
                    descfile = None

                    if desc_folder:
                        desc_folder = Path(desc_folder)
                        if desc_folder.is_dir():
                            potential_file = desc_folder / f"{uuid}.txt"
                            if potential_file.is_file():
                                descfile = potential_file

                    if descfile is None and path.exists():
                        if path.is_file():
                            descfile = path.with_suffix(".txt")
                            if not descfile.is_file():
                                descfile = path.parent / f"{uuid}.txt"
                        elif path.is_dir():
                            descfile = path / f"{uuid}.txt"
                            if not descfile.is_file():
                                descfile = path / "description.txt"

                    if descfile and descfile.is_file():
                        async with aiofiles.open(descfile, "r", encoding="utf-8") as f:
                            descfile_content = await f.read()
                        if not any(line.strip().startswith("# USE:") for line in descfile_content.splitlines()):
                            descfile_content = "# USE: full\n" + descfile_content
                        await description.write("\n" + descfile_content + "\n")
                        meta["description"] = "CUSTOM"
                        custom_desc_written = True

                # Manual desc
                desc = meta.get("desc")
                if desc and desc != descfile_content:
                    await description.write("\n" + desc + "\n")
                    meta["description"] = "CUSTOM"
                    custom_desc_written = True

                # External descfile
                descfile_path = meta.get("descfile")
                if descfile_path:
                    p = Path(descfile_path)
                    if p.is_file():
                        async with aiofiles.open(p, "r", encoding="utf-8") as f:
                            descfile_text = await f.read()
                        await description.write("\n" + descfile_text + "\n")
                        meta["description"] = "CUSTOM"
                        custom_desc_written = True

                # Final fallback: write meta["desc"] if present
                if meta.get("desc"):
                    await description.write("\n" + meta["desc"] + "\n")
                    meta["description"] = "CUSTOM"
                    custom_desc_written = True

        if self.pm:
            self.pm.update("Description generation complete")

        return meta

    async def edit_desc(self, meta: Dict, tracker: str, comparison: bool = False, desc_header: str = "", whitelist: List[str] = None):
        """Main method to format description for trackers."""
        # Read base description
        base_description_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "DESCRIPTION.txt"
        async with aiofiles.open(base_description_path, "r", encoding="utf-8") as basefile:
            base = await basefile.read()

        # Parse control markers
        control = DescControl()
        lines = base.splitlines()
        filtered_lines = []
        for line in lines:
            if line.strip().startswith("# USE:"):
                control = DescControl.from_markers(line.strip())
                # Don't add the control line to the base content
            else:
                filtered_lines.append(line)
        base = "\n".join(filtered_lines)

        # Open output file
        output_description_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{tracker}]DESCRIPTION.txt"
        async with aiofiles.open(output_description_path, "w", encoding="utf-8") as descfile:
            if desc_header:
                await descfile.write(desc_header)

            bbcode = BBCODE()

            # Write media elements (trailer/logo)
            if (
                not control.as_is
                and (control.include_trailer or control.include_logo)
                and (meta.get("description") not in ("BLU", "PTP"))
            ):
                await self._write_media_elements(descfile, meta, tracker, control)

            # Write disc info if enabled
            if control.include_disc_info and not control.as_is:
                await self._write_disc_info(descfile, meta.get("discs", []))

            # Process base description
            if control.as_is:
                await descfile.write(base)
            else:
                desc = bbcode.convert_pre_to_code(base)
                desc = bbcode.convert_hide_to_spoiler(desc)
                if tracker in ("LDU", "BLU", "LST", "RHD", "RF", "AITHER"):
                    comparison = True
                if not comparison:
                    desc = bbcode.convert_comparison_to_collapse(desc, 1000)
                await descfile.write(desc)

            # Handle music Description
            if meta.get("is_music", False):
                await self.create_music_description(meta, tracker)
                return  # Music has its own flow, skip further processing

            # Handle XXX Description
            if meta.get("xxx", False):
                await self._xxx_description(descfile, meta, tracker)

            # Write images
            if control.include_screens and not control.as_is:
                await self._write_images(descfile, meta, tracker, whitelist, control)

            # Write signature
            if control.include_signature and not control.as_is:
                await self._write_signature(descfile, meta, tracker)

        return

    async def _write_disc_info(self, descfile, discs: List[Dict]):
        """Helper method to write disc information"""
        if not discs:
            return

        if discs[0]["type"] == "DVD":
            await descfile.write(f"[spoiler=VOB MediaInfo][code]{discs[0]['vob_mi']}[/code][/spoiler]\n\n")

        if len(discs) >= 2:
            for disc in discs[1:]:
                if disc["type"] == "BDMV":
                    await descfile.write(f"[spoiler={disc.get('name', 'BDINFO')}][code]{disc['summary']}[/code][/spoiler]\n\n")
                elif disc["type"] == "DVD":
                    await descfile.write(f"{disc['name']}:\n")
                    await descfile.write(
                        f"[spoiler={os.path.basename(disc['vob'])}][code][{disc['vob_mi']}[/code][/spoiler] "
                        f"[spoiler={os.path.basename(disc['ifo'])}][code][{disc['ifo_mi']}[/code][/spoiler]\n\n"
                    )
                elif disc["type"] == "HDDVD":
                    await descfile.write(f"{disc['name']}:\n")
                    await descfile.write(
                        f"[spoiler={os.path.basename(disc['largest_evo'])}][code][{disc['evo_mi']}[/code][/spoiler]\n\n"
                    )

    async def _write_media_elements(self, descfile, meta: Dict, tracker: str, control: DescControl):
        """Helper method to write trailer and logo based on control"""
        if control.include_trailer and tracker not in ("AITHER", "CBR", "OE", "TMG", "TL"):
            # Originally MOVIE-only — TMDb has trailers for TV/anime too
            # (confirmed: One Piece's own TMDb entry has one), the category
            # gate was just never extended. meta["youtube"] is already
            # resolved at the SERIES level for a season/series upload (its
            # own tmdb id, not a per-episode one), so no extra lookup needed.
            if self.config["DEFAULT"].get("add_trailer", False) and meta.get("category") in ("MOVIE", "TV"):
                if key := meta.get("youtube"):
                    await descfile.write(f"[center][youtube]{key}[/youtube][/center]")

        if control.include_logo and self.config["DEFAULT"].get("add_logo", False):
            if logo := meta.get("logo"):
                logo_size = self.config["DEFAULT"].get("logo_size", 420)
                await descfile.write(f"[center][img={logo_size}]{logo}[/img][/center]")

    async def _get_valid_images(self, meta: Dict, tracker: str, whitelist: List[str] = None):
        """Helper method to get valid images based on whitelist."""
        whitelist = whitelist if whitelist is not None else []
        num_screens = int(meta.get("screens", 0)) or 3
        per_episode = self.config["SCREENS"].get("per_episode", 3) if meta.get("per_episode_screens", True) else 0

        normalized_whitelist = {host.split(".")[0] if "." in host else host for host in whitelist}

        # Initialize image lists
        image_list = meta.get("image_list")
        if isinstance(image_list, list):  # backwards compatibility
            image_list = {"main": image_list, "episodes": {}, "contact_sheets": []}
        rehost_list = meta.get("rehost_list", {})
        result = {"main": [], "episodes": {}, "contact_sheets": []}

        if "contact_sheets" in image_list:
            result["contact_sheets"] = image_list["contact_sheets"]

        # If whitelist is empty or None, use all images from image_list
        if not whitelist:
            result["main"] = image_list["main"][:num_screens]
            if per_episode > 0:
                for ep_name, ep_images in image_list["episodes"].items():
                    result["episodes"][ep_name] = ep_images[:per_episode]
            log.debug(
                f"No whitelist for tracker {tracker}, using {len(result['main'])} main images and {len(result['episodes'])} episode sets from image_list"
            )
            return result

        # Helper function to check if an image is valid for the whitelist
        def is_valid_image(image):
            if not all(image.get(k) for k in ("img_url", "raw_url", "web_url")):
                return False
            domain = urlparse(image["web_url"]).hostname or ""
            domain_key = domain.split(".")[0] if domain else ""
            return domain in whitelist or domain_key in normalized_whitelist

        # Check image_list first
        for image in image_list["main"]:
            if is_valid_image(image):
                result["main"].append(image)
            if len(result["main"]) >= num_screens:
                break

        if per_episode > 0:
            for ep_name, ep_images in image_list["episodes"].items():
                valid_ep_images = []
                for image in ep_images:
                    if is_valid_image(image):
                        valid_ep_images.append(image)
                    if len(valid_ep_images) >= per_episode:
                        break
                if valid_ep_images:
                    result["episodes"][ep_name] = valid_ep_images

        # Fall back to rehost_list if needed
        if len(result["main"]) < num_screens or (
            per_episode > 0 and any(len(ep_images) < per_episode for ep_images in result["episodes"].values())
        ):
            for host_key in rehost_list:
                if host_key not in normalized_whitelist:
                    continue
                rehost_images = rehost_list[host_key]
                # Fill main images
                if len(result["main"]) < num_screens:
                    for image in rehost_images.get("main", []):
                        if is_valid_image(image):
                            result["main"].append(image)
                        if len(result["main"]) >= num_screens:
                            break
                # Fill episode images
                if per_episode > 0:
                    for ep_name, ep_images in rehost_images.get("episodes", {}).items():
                        if ep_name in result["episodes"] and len(result["episodes"][ep_name]) >= per_episode:
                            continue
                        valid_ep_images = result["episodes"].get(ep_name, [])
                        for image in ep_images:
                            if is_valid_image(image):
                                valid_ep_images.append(image)
                            if len(valid_ep_images) >= per_episode:
                                break
                        if valid_ep_images:
                            result["episodes"][ep_name] = valid_ep_images

        # Log warnings if insufficient images
        if len(result["main"]) < num_screens:
            log.warning(
                f"Tracker {tracker}: Found only {len(result['main'])} of {num_screens} required main images matching whitelist {whitelist}"
            )
        if per_episode > 0:
            for ep_name, ep_images in result["episodes"].items():
                if len(ep_images) < per_episode:
                    log.warning(
                        f"Tracker {tracker}: Found only {len(ep_images)} of {per_episode} required images for episode {ep_name} matching whitelist {whitelist}"
                    )

        log.debug(
            f"Tracker {tracker}: Selected {len(result['main'])} main images and {len(result['episodes'])} episode sets matching whitelist {whitelist}"
        )
        return result

    async def _xxx_description(self, descfile, meta: Dict, tracker: str):
        """Helper method to write XXX description."""
        porndb = meta.get("porndb") or {}
        trailer = meta.get("trailer_url") if meta.get("trailer_url") != "" else None
        if trailer:
            await descfile.write(f"[center][img]{trailer}[/img][/center]\n\n")
        if porndb:
            desc = porndb.get("description", "").strip() or "N/A"
            perf = porndb.get("performers", "").strip() or "N/A"
            await descfile.write(f"[h4][color=#91FFF7]DESCRIPTION[/color][/h4][b]{desc}[/b]\n\n")
            await descfile.write(f"[h4][color=#91FFF7]PERFORMERS[/color][/h4][b]{perf}[/b]\n\n")
        if tracker == "LDU":
            # LDU doesn't support api cover/banner upload they have a workaround but it must be .jpg
            cover = next(
                (url for url in [porndb.get("poster"), meta.get("cover_url")] if url and url.endswith((".jpg", ".jpeg"))),
                porndb.get("poster") or meta.get("cover_url"),
            )

            banner = next(
                (url for url in [porndb.get("image"), meta.get("banner_url")] if url and url.endswith((".jpg", ".jpeg"))),
                porndb.get("image") or meta.get("banner_url"),
            )

            await descfile.write(f"[url=torrent-cover={cover} ][/url] [url=torrent-banner={banner} ][/url]")
        return

    async def _write_images(
        self, descfile, meta: Dict, tracker: str, whitelist: List[str] = None, control: DescControl = None, img_size: int = None
    ):
        """Helper method to write images."""
        if meta.get("screens", 0) == 0:
            return
        images = await self._get_valid_images(meta, tracker, whitelist)
        if not images["main"] and not images["episodes"]:
            return

        if img_size is None:
            img_size = 350 if tracker in ("CBR", "TVC", "PTP") else self.config["DEFAULT"].get("img_size", 500)
        if control and not control.modify_img_size:
            img_size = ""  # No size modification
        if int(img_size) == 0:
            img_size = ""

        inline_imgs = int(self.config["DEFAULT"].get("inline_imgs", 0)) or 0

        # Write main images
        await descfile.write("\n[center]")
        for i, image in enumerate(images["main"]):
            try:
                img_tag = f"[img={img_size}]" if img_size else "[img]"
                if tracker in ("PTP", "TMG"):
                    await descfile.write(f"[img]{image['raw_url']}[/img]")
                elif tracker == "TL":
                    await descfile.write(f"[url={image['web_url']}][img]{image['img_url']}[/img][/url]")
                else:
                    await descfile.write(f"[url={image['web_url']}]{img_tag}{image['raw_url']}[/img][/url] ")
                if inline_imgs and i % inline_imgs == inline_imgs - 1:
                    await descfile.write("\n")
            except (KeyError, TypeError):
                continue
        await descfile.write("[/center]")

        # Write episode images
        if meta.get("tv_pack") and meta.get("per_episode_screens", True) and images["episodes"]:
            await descfile.write("\n[center][spoiler=Episode Screens]")
            for ep_name, ep_images in images["episodes"].items():
                if ep_name.upper() == "E01":
                    continue  # E01 was already included in main images
                display_name = "OVA" if ep_name == "OVA" else f"Episode {int(ep_name[1:])}"
                await descfile.write(f"\n[spoiler={display_name}][center]")
                for i, image in enumerate(ep_images):
                    try:
                        img_tag = f"[img={img_size}]" if img_size else "[img]"
                        await descfile.write(f"[url={image['web_url']}]{img_tag}{image['raw_url']}[/img][/url] ")
                        if inline_imgs and i % inline_imgs == inline_imgs - 1:
                            await descfile.write("\n")
                    except (KeyError, TypeError):
                        continue
                await descfile.write("[/center][/spoiler]")
            await descfile.write("\n[/spoiler][/center]")

        # Write contact sheet
        if meta.get("xxx") and not self.config["SCREENS"].get("contact_sheet", {}).get("skip"):
            await descfile.write("\n[center]")
            for i, image in enumerate(images["contact_sheets"]):
                try:
                    # img_tag = f"[img={self.config['SCREENS'].get('ADULT', {}).get('contact_size', 800)}]" if img_size else "[img]"
                    img_tag = "[img]"
                    await descfile.write(f"{img_tag}{image['raw_url']}[/img]")
                except (KeyError, TypeError):
                    continue
            await descfile.write("[/center]")

    async def _write_signature(self, descfile, meta: Dict, tracker: str):
        """Helper method to write signature."""
        use_global_sigs = self.config["SIGNATURES"].get("use_global_sigs", "default")

        if use_global_sigs is None:
            return  # easy mode for NO SIGS!
        elif use_global_sigs is True:
            sig_config = self.config["SIGNATURES"]
        elif use_global_sigs is False:
            sig_config = self.config["TRACKERS"].get(tracker, {})
        else:
            tracker_config = self.config["TRACKERS"].get(tracker, {})
            sig_config = tracker_config

        xanon = meta.get("xxx") and self.config["TRACKERS"].get(tracker, {}).get("xanon", False)
        is_anon = meta.get("anon", 0) != 0 or self.config["TRACKERS"].get(tracker, {}).get("anon", False) or xanon
        is_pr = meta.get("personal_release", False)
        sig_key = "anon_pr_sig" if is_anon and is_pr else "pr_signature" if is_pr else "anon_signature" if is_anon else "signature"

        if not tracker_config.get(sig_key):
            sig_config = self.config["SIGNATURES"]

        sig_types = {
            "signature": sig_config.get("signature"),
            "anon_signature": sig_config.get("anon_signature"),
            "pr_signature": sig_config.get("pr_signature"),
            "anon_pr_sig": sig_config.get("anon_pr_signature"),
        }

        # Write signature if it exists
        if sig := sig_types.get(sig_key):
            await descfile.write(f"\n{sig}")

    async def create_music_description(self, meta: Dict, tracker: str):
        """Helper method to create music-specific description."""
        output_description_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{tracker}]DESCRIPTION.txt"
        async with aiofiles.open(output_description_path, "w", encoding="utf-8") as descfile:

            def center_img(url, size=120, link=None):
                img = f"[img={size}]{url}[/img]" if size else f"[img]{url}[/img]"
                return f"[center]{f'[url={link}]{img}[/url]' if link else img}[/center]\n\n"

            album_cover = meta.get("album_cover")
            album_back = meta.get("album_back")
            display_cover = self.config["DEFAULT"].get("album_covers", 2)

            if display_cover >= 1:
                if album_cover:
                    img_line = f"[center][img]{album_cover}[/img]"
                    if display_cover >= 2 and album_back:
                        img_line += f" [img]{album_back}[/img]"
                    await descfile.write(img_line + "[/center]\n\n")
                elif album_back:
                    await descfile.write(center_img(album_back, size=0))

            proof = meta.get("proof_url")
            if proof:
                await descfile.write(f"[center][spoiler=Proof][center][img=500]{proof}[/img][/center][/spoiler][/center]\n\n")

            # Logos with URLs
            logos = {
                "mbid": (
                    "https://musicbrainz.org/release/{}",
                    r"https://upload.wikimedia.org/wikipedia/commons/f/f2/MusicBrainz_Logo_Mini_%282016%29.svg",
                ),
                "discogs_id": (
                    "https://www.discogs.com/release/{}",
                    "https://upload.wikimedia.org/wikipedia/commons/thumb/c/ce/Discogs_logo_black.svg/2880px-Discogs_logo_black.svg.png",
                ),
                "discogs_master": (
                    "https://www.discogs.com/master/{}",
                    "https://upload.wikimedia.org/wikipedia/commons/thumb/c/ce/Discogs_logo_black.svg/2880px-Discogs_logo_black.svg.png",
                ),
                "discogs_url": (
                    "{}",
                    "https://upload.wikimedia.org/wikipedia/commons/thumb/c/ce/Discogs_logo_black.svg/2880px-Discogs_logo_black.svg.png",
                ),
                "allmusic": (
                    "{}",
                    "https://upload.wikimedia.org/wikipedia/commons/thumb/0/03/AllMusic_Text_Logo.svg/2880px-AllMusic_Text_Logo.svg.png",
                ),
                "wikidata": ("{}", "https://upload.wikimedia.org/wikipedia/commons/6/66/Wikidata-logo-en.svg"),
                "genius": ("{}", "https://upload.wikimedia.org/wikipedia/commons/c/cd/Genius-Wordmark.svg"),
                "deezer": ("{}", "https://upload.wikimedia.org/wikipedia/commons/7/75/Deezer_logo%2C_2023.svg"),
                "apple_music": ("{}", "https://upload.wikimedia.org/wikipedia/commons/2/2a/Apple_Music_logo.svg"),
            }

            for key, (url_tpl, img_url) in logos.items():
                if key == "deezer":
                    val = meta.get("deezer_info", {}).get("link")
                elif key == "apple_music":
                    other_dbs = meta.get("mb_info", {}).get("relations", {}).get("other_databases", [])
                    if other_dbs:
                        if isinstance(other_dbs, list):
                            urls = other_dbs
                        else:
                            urls = [u.strip() for u in other_dbs.split(",")]
                        val = next((u for u in urls if "https://music.apple.com/" in u), None)
                    else:
                        val = None
                else:
                    val = meta.get(key)
                if val:
                    url = url_tpl.format(val)
                    await descfile.write(center_img(img_url, link=url))

            # Tracklist and logs # TODO add on foo_DR + auCDtect
            tracks = meta.get("tracklist", {})
            if tracks:
                await descfile.write("\n[code][h5]TRACKLIST[/h5]\n")

                if len(tracks) > 1:
                    for idx, (disc, disc_tracks) in enumerate(tracks.items()):
                        await descfile.write(f"[u]{disc}[/u]\n")
                        max_len = max(len(track) for track in disc_tracks)
                        for track, time in disc_tracks.items():
                            await descfile.write(f"{track}{' ' * (max_len - len(track) + 2)}[{time}]\n")

                        log_files = meta.get("log_files", [])
                        if len(log_files) > idx:
                            log = await self.read_log_file(log_files[idx])
                            if log:
                                await descfile.write(f"\n[spoiler=log][code]{log}[/code][/spoiler]\n")
                        await descfile.write("\n")
                else:
                    disc_tracks = next(iter(tracks.values()))
                    for track, time in disc_tracks.items():
                        await descfile.write(f"{track}{' ' * (52 - len(track))}[{time}]\n")
                    log_files = meta.get("log_files", [])
                    if log_files:
                        log = await self.read_log_file(log_files[0])
                        if log:
                            await descfile.write(f"\n[spoiler=log][code]{log}[/code][/spoiler]")

                await descfile.write("[/code]\n")

            if album_cover and tracker == "LDU":
                await descfile.write(f"[url=torrent-cover={album_cover} ][/url]")

            return

    async def ptgen(self, meta: Dict, ptgen_site="", ptgen_retry=3):
        """Ptgen fetching logic."""
        ptgen = ""
        url = "https://ptgen.zhenzhen.workers.dev"
        if ptgen_site != "":
            url = ptgen_site
        params = {}
        data = {}

        pm = ProgressManager.get_instance()

        async with httpx.AsyncClient(timeout=60.0) as client:
            if int(meta.get("imdb_id", "0")) != 0:
                params = {"search": meta["imdb"], "source": "imdb"}
                try:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    json_response = response.json()

                    if json_response.get("success") and "data" in json_response and len(json_response["data"]) > 0:
                        params["url"] = json_response["data"][0]["link"]
                        console.print(f"Extracted Douban URL: {params['url']}")
                    elif json_response.get("success") and "imdb_id" in json_response:
                        console.print("Using direct IMDB data from response")
                        meta["ptgen"] = json_response
                    else:
                        console.print(f"API returned error: {json_response.get('error', 'Unknown error')}")

                except Exception as e:
                    console.print(f"Error parsing response: {e}")

                if not params.get("url"):
                    try:
                        response = await client.get(url, params=params)
                        response.raise_for_status()
                        params["url"] = response.json()["data"][0]["link"]
                        console.print(f"Extracted Douban URL: {params['url']}")
                    except Exception as e:
                        console.print(f"Error extracting Douban URL: {e}")
                        console.print("[red]Unable to get data from ptgen using IMDb")
                        if meta.get("unattended", False):
                            return ""
                        if pm:
                            with pm.pause_for_interaction():
                                params["url"] = console.input("[red]Please enter [yellow]Douban[/yellow] link: ")
                        else:
                            params["url"] = console.input("[red]Please enter [yellow]Douban[/yellow] link: ")
            else:
                console.print("[red]No IMDb id was found.")
                if meta.get("unattended", False):
                    return ""
                if pm:
                    with pm.pause_for_interaction():
                        params["url"] = console.input("[red]Please enter [yellow]Douban[/yellow] link: ")
                else:
                    params["url"] = console.input("[red]Please enter [yellow]Douban[/yellow] link: ")

            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                json_response = response.json()

                if json_response.get("error") is not None:
                    for retry in range(ptgen_retry):
                        response = await client.get(url, params=params)
                        response.raise_for_status()
                        if response.json().get("error") is None:
                            break

                ptgen = response.json()
                meta["ptgen"] = ptgen
                async with aiofiles.open(f"{meta['base_dir']}/tmp/{meta['uuid']}/meta.json", "w") as f:
                    await f.write(json.dumps(meta, indent=4))

                ptgen = ptgen["format"]
                if "[/img]" in ptgen:
                    ptgen = ptgen.split("[/img]")[1]
                ptgen = f"[img]{meta.get('imdb_info', {}).get('cover', meta.get('cover', ''))}[/img]{ptgen}"
            except Exception:
                console.print_exception()
                console.print(response.text if "response" in locals() else "No response")
                console.print("[bold red]There was an error getting the ptgen \nUploading without ptgen")
                return ""

        return ptgen
