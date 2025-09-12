import json
import os
import re
import traceback
from dataclasses import dataclass
from urllib.parse import urlparse

import aiofiles
import requests

from src.bbcode import BBCODE
from src.progress import ProgressManager
from src.utils.console import console, log


class COMMON:
    def __init__(self, config):
        self.config = config
        pass

    async def read_log_file(self, log_file):
        try:
            async with aiofiles.open(log_file, "rb") as log:
                raw_data = await log.read()
                try:
                    log_contents = raw_data.decode("utf-16")
                except UnicodeDecodeError:
                    try:
                        log_contents = raw_data.decode("utf-8")
                    except UnicodeDecodeError:
                        log_contents = raw_data.decode("latin-1", errors="ignore")

                return log_contents
        except Exception as e:
            return f"Error reading log file: {str(e)}"

    async def unit3d_edit_desc(self, meta, tracker, comparison=False, desc_header="", whitelist=None):
        # Read base description
        async with aiofiles.open(f"{meta['base_dir']}/tmp/{meta['uuid']}/DESCRIPTION.txt", "r", encoding="utf-8") as basefile:
            base = await basefile.read()

        # Parse control markers
        control = DescControl()
        for line in base.splitlines():
            if line.strip().startswith("# USE:"):
                control = DescControl.from_markers(line.strip())
                base = "\n".join(l for l in base.splitlines() if not l.strip().startswith("# USE:"))  # noqa: E741
                break

        # Open output file
        async with aiofiles.open(
            f"{meta['base_dir']}/tmp/{meta['uuid']}/[{tracker}]DESCRIPTION.txt", "w", encoding="utf-8"
        ) as descfile:
            if desc_header:
                await descfile.write(desc_header)

            bbcode = BBCODE()

            # Write media elements (trailer/logo)
            if not control.as_is and (control.include_trailer or control.include_logo):
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

            # Handle music description
            if meta.get("is_music", False):
                await self.create_music_description(meta, tracker)
                return  # Music has its own flow, skip further processing

            # Write images
            if control.include_screens and not control.as_is:
                await self._write_images(descfile, meta, tracker, whitelist, control)

            # Write signature
            if control.include_signature and not control.as_is:
                await self._write_signature(descfile, meta, tracker)

        return

    async def _write_disc_info(self, descfile, discs):
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

    async def _write_media_elements(self, descfile, meta, tracker, control):
        """Helper method to write trailer and logo based on control"""
        if control.include_trailer and tracker not in ("AITHER", "CBR", "OE", "TMG"):
            if self.config["DEFAULT"].get("add_trailer", False) and meta.get("category") == "MOVIE":
                if key := meta.get("youtube"):
                    await descfile.write(f"[center][youtube]{key}[/youtube][/center]")

        if control.include_logo and self.config["DEFAULT"].get("add_logo", False):
            if logo := meta.get("logo"):
                logo_size = self.config["DEFAULT"].get("logo_size", 420)
                await descfile.write(f"[center][img={logo_size}]{logo}[/img][/center]")

    async def _get_valid_images(self, meta, tracker, whitelist=None):
        whitelist = whitelist if whitelist is not None else []
        num_screens = int(meta.get("screens", 0)) or 3
        per_episode = self.config["SCREENS"].get("per_episode", 3) if meta.get("per_episode_screens", True) else 0

        normalized_whitelist = {host.split(".")[0] if "." in host else host for host in whitelist}

        # Initialize image lists
        image_list = meta.get("image_list")
        if isinstance(image_list, list):  # backwards compatibility
            image_list = {"main": image_list, "episodes": {}}
        rehost_list = meta.get("rehost_list", {})
        result = {"main": [], "episodes": {}}

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

    async def _write_images(self, descfile, meta, tracker, whitelist=None, control=None):
        if meta.get("screens", 0) == 0:
            return

        images = await self._get_valid_images(meta, tracker, whitelist)

        if not images["main"] and not images["episodes"]:
            return

        img_size = 350 if tracker == "CBR" else self.config["DEFAULT"].get("img_size", 500)
        if not control.modify_img_size:
            img_size = ""  # No size modification
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

        # Write episode images in spoilers if requested
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

    async def _write_signature(self, descfile, meta, tracker):
        """Helper method to write signature"""
        use_global_sigs = self.config["SIGNATURES"].get("use_global_sigs", "default")

        if use_global_sigs is None:
            return  # easy mode for NO SIGS!
        elif use_global_sigs is True:
            config_section = "DEFAULT"
            sig_config = self.config[config_section]
        elif use_global_sigs is False:
            config_section = "TRACKERS"
            sig_config = self.config[config_section].get(tracker, {})
        else:  # Tracker Specific, falling back to Global
            config_section = "TRACKERS"
            sig_config = (
                self.config[config_section].get(tracker, {}) if self.config[config_section].get(tracker) else self.config["DEFAULT"]
            )

        sig_types = {
            "signature": sig_config.get("signature"),
            "anon_signature": sig_config.get("anon_signature"),
            "pr_signature": sig_config.get("pr_signature"),
            "anon_pr_sig": sig_config.get("anon_pr_signature"),
        }

        is_anon = meta.get("anon", 0) != 0 or self.config["TRACKERS"].get(tracker, {}).get("anon", False)
        is_pr = meta.get("personal_release", False)
        sig_key = "anon_pr_sig" if is_anon and is_pr else "pr_signature" if is_pr else "anon_signature" if is_anon else "signature"

        # Write signature if it exists
        if sig := sig_types.get(sig_key):
            await descfile.write(f"\n{sig}")

    async def create_music_description(self, meta, tracker):
        async with aiofiles.open(
            f"{meta['base_dir']}/tmp/{meta['uuid']}/[{tracker}]DESCRIPTION.txt", "w", encoding="utf-8"
        ) as descfile:

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

            if proof := meta.get("album_proof"):
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
            }

            for key, (url_tpl, img_url) in logos.items():
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

    async def unit3d_torrent_info(self, tracker, torrent_url, id):
        tmdb = imdb = tvdb = description = category = infohash = mal = None
        imagelist = []
        params = {"api_token": self.config["TRACKERS"][tracker].get("api_key", "")}
        url = f"{torrent_url}{id}"
        response = requests.get(url=url, params=params)
        try:
            response = response.json()
            attributes = response["attributes"]
            category = attributes.get("category")
            description = attributes.get("description")
            tmdb = attributes.get("tmdb_id")
            tvdb = attributes.get("tvdb_id")
            mal = attributes.get("mal_id")
            imdb = attributes.get("imdb_id")
            infohash = attributes.get("info_hash")

            bbcode = BBCODE()
            description, imagelist = bbcode.clean_unit3d_description(description, torrent_url)
            console.print(f"[green]Successfully grabbed description from {tracker}")
        except Exception:
            console.print(traceback.print_exc())
            console.print(f"[yellow]Invalid Response from {tracker} API.")

        return tmdb, imdb, tvdb, mal, description, category, infohash, imagelist

    async def parseCookieFile(self, cookiefile):
        """Parse a cookies.txt file and return a dictionary of key value pairs
        compatible with requests."""

        cookies = {}
        with open(cookiefile, "r") as fp:
            for line in fp:
                if not line.startswith(("# ", "\n", "#\n")):
                    lineFields = re.split(" |\t", line.strip())
                    lineFields = [x for x in lineFields if x != ""]
                    cookies[lineFields[5]] = lineFields[6]
        return cookies

    def gazelle_bbcode_bbcode(self, desc):
        desc = desc.replace("[spoiler", "[hide").replace("[/spoiler]", "[/hide]")
        desc = desc.replace("[center]", "[align=center]").replace("[/center]", "[/align]")
        desc = desc.replace("[left]", "[align=left]").replace("[/left]", "[/align]")
        desc = desc.replace("[right]", "[align=right]").replace("[/right]", "[/align]")
        desc = desc.replace("[code]", "[quote]").replace("[/code]", "[/quote]")
        return desc

    async def filter_dupes(self, dupes, meta):
        if meta["debug"]:
            console.log("[cyan]Pre-filtered dupes")
            console.log(dupes)

        new_dupes = {}
        for each in dupes:
            remove_set = set({meta.get("resolution")}) if meta.get("sd", 0) != 1 else set()
            search_combos = [
                {"search": meta.get("hdr", ""), "search_for": {"HDR", "PQ10"}, "update": {"HDR|PQ10"}},
                {"search": meta.get("hdr", ""), "search_for": {"DV"}, "update": {"DV|DoVi"}},
                {"search": meta.get("hdr", ""), "search_not": {"DV", "DoVi", "HDR", "PQ10"}, "update": {"!(DV)|(DoVi)|(HDR)|(PQ10)"}},
                {"search": str(meta.get("tv_pack", 0)), "search_for": "1", "update": {rf"{meta.get('season')}(?!E\d+)"}},
                {
                    "search": meta.get("episode", ""),
                    "search_for": meta.get("episode", ""),
                    "update": {meta.get("season", ""), meta.get("episode", "")},
                },
            ]
            search_matches = [{"if": {"REMUX", "WEBDL", "WEBRip", "HDTV"}, "in": meta.get("type", "")}]

            for s in search_combos:
                if isinstance(s.get("search"), str) and s.get("search_for"):
                    if any(re.search(x, s["search"], flags=re.IGNORECASE) for x in s["search_for"]):
                        remove_set.update(s["update"])
                if isinstance(s.get("search_not"), str) and s.get("search_not"):
                    if not any(re.search(x, s["search"], flags=re.IGNORECASE) for x in s["search_not"]):
                        remove_set.update(s["update"])

            for sm in search_matches:
                for a in sm["if"]:
                    if a in sm["in"]:
                        remove_set.add(a)

            search = each.lower().replace("-", "").replace(" ", "").replace(".", "")
            for x in remove_set.copy():
                if x:
                    if "|" in x:
                        look_for = x.split("|")
                        for y in look_for:
                            if y.lower() in search:
                                remove_set.discard(x)
                                remove_set.add(y)

            allow = True
            for x in remove_set:
                if x:
                    if not x.startswith("!"):
                        if not re.search(x, search, flags=re.I):
                            allow = False
                    else:
                        if re.search(x.replace("!", "", 1), search, flags=re.I) not in (None, False):
                            allow = False
            if allow and each not in new_dupes:
                new_dupes[each] = dupes[each]
        return new_dupes


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
