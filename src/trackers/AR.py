import json
import pickle
import platform
import re
import urllib.parse
from pathlib import Path
from typing import Dict, Optional

import aiofiles
import httpx
import rich.prompt as Prompt
from bs4 import BeautifulSoup
from torf import Torrent

from src.bbcode import BBCODE
from src.mediainfo import MediaInfoHandler
from src.trackers.COMMON import COMMON
from src.utils.console import log
from src.utils.torrentman import edit_torrent


class AR:
    def __init__(self, config):
        self.config = config
        self.type = "other"
        self.session = None
        self.tracker = "AR"
        self.source_flag = "AlphaRatio"
        self.username = config["TRACKERS"]["AR"].get("username", "").strip()
        self.password = config["TRACKERS"]["AR"].get("password", "").strip()
        pid = config["TRACKERS"][self.tracker].get("PID", "PASSKEY_HERE")
        self.announce_url = f"http://tracker.alpharatio.cc:2710/{pid}/announce"
        self.login_url = "https://alpharatio.cc/login.php"
        self.upload_url = "https://alpharatio.cc/upload.php"
        self.search_url = "https://alpharatio.cc/torrents.php"
        self.user_agent = f"Uploadrr ({platform.system()} {platform.release()})"
        self.signature = None
        self.banned_groups = []

    async def start_session(self):
        if self.session is not None:
            log.warning("Previous session was not closed properly. Closing it now.")
            await self.close_session()
        self.session = httpx.AsyncClient()

    async def close_session(self):
        if self.session is not None:
            await self.session.aclose()
            self.session = None

    async def validate_credentials(self, meta: Dict) -> bool:
        if self.session:
            log.warning("Previous session was not closed properly. Using existing session.")
        else:
            await self.start_session()

        if await self.load_session(meta):
            response = await self.get_initial_response()
            if await self.validate_login(response):
                return True
        else:
            log.info("No session file found. Attempting to log in...")
            if await self.login(meta):
                log.info("Login successful, session file created.")
                await self.save_session(meta)
                return True
            else:
                log.error("Failed to validate credentials. Please confirm site status and credentials.")
        return False

    async def get_initial_response(self) -> str:
        try:
            response = await self.session.get(self.login_url)
            response.raise_for_status()
            return response.text
        except httpx.RequestError as e:
            log.error(f"Failed to get initial response: {e}")
            raise

    async def validate_login(self, response_text: str) -> bool:
        if "login.php?act=recover" in response_text:
            log.error("Login failed. Check your credentials.")
            return False
        return True

    async def login(self, meta: Dict) -> bool:
        data = {"username": self.username, "password": self.password, "keeplogged": "1", "login": "Login"}
        try:
            response = await self.session.post(self.login_url, data=data)
            response.raise_for_status()
            if await self.validate_login(response.text):
                await self.save_session(meta)
                return True
            return False
        except httpx.RequestError as e:
            log.error(f"Login failed: {e}")
            return False

    async def save_session(self, meta: Dict):
        session_file = Path(meta["base_dir"]) / "data" / "cookies" / f"{self.tracker}.pkl"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        cookies = self.session.cookies
        cookie_dict = dict(cookies)

        with session_file.open("wb") as f:
            pickle.dump(cookie_dict, f)

    async def load_session(self, meta: Dict) -> bool:
        session_file = Path(meta["base_dir"]) / "data" / "cookies" / f"{self.tracker}.pkl"
        retry_count = 0
        max_retries = 2

        while retry_count < max_retries:
            try:
                with session_file.open("rb") as f:
                    cookie_dict = pickle.load(f)
                    self.session.cookies.update(cookie_dict)

                    if meta.get("debug", False):
                        log.debug(f"Loaded cookies: {cookie_dict}")

                    response = await self.session.get("https://alpharatio.cc/torrents.php")
                    response.raise_for_status()
                    return True

            except (FileNotFoundError, EOFError, pickle.UnpicklingError, httpx.RequestError) as e:
                log.warning(f"Session loading error: {e}. Retrying...")
                await self.close_session()
                await self.start_session()
                retry_count += 1

        log.info("Failed to reuse session after retries. Attempting fresh login...")
        await self.close_session()
        await self.start_session()
        return await self.login(meta)

    async def edit_desc(self, meta: Dict):
        bbcode = BBCODE()
        desc_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]DESCRIPTION.txt"

        if meta.get("is_music", False):
            common = COMMON(config=self.config)
            desc = await common.create_music_description(meta, self.tracker)
            async with aiofiles.open(desc_path, "r", encoding="utf-8") as file:
                desc = await file.read()
            desc = bbcode.clean_for_ar(desc)
            async with aiofiles.open(desc_path, "w", encoding="utf-8") as file:
                await file.write(desc)
        else:
            base_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "DESCRIPTION.txt"
            async with aiofiles.open(base_path, "r", encoding="utf-8") as base_file:
                base = await base_file.read()
            base = bbcode.clean_for_ar(base)

            async with aiofiles.open(desc_path, "w", encoding="utf-8") as desc:
                mihandle = MediaInfoHandler()
                micache = mihandle._cache.get(meta["uuid"])
                mi = micache["media"]["track"]
                filename = next((track["FileNameExtension"] for track in mi if track["@type"] == "General"), "N/A")
                duration = next((track["Duration_String3"] for track in mi if track["@type"] == "General"), "N/A")
                video_info = next((track for track in mi if track["@type"] == "Video"), {})
                video_encode = meta.get("video_encode", "")
                audio_info = next((track for track in mi if track["@type"] == "Audio"), {})
                subtitles_list = next((track.get("Text_Language_List", "N/A") for track in mi if track["@type"] == "General"), "N/A")
                subtitles = ", ".join(sorted(set(subtitles_list.split(" / ")))) if subtitles_list != "N/A" else "N/A"

                resolution = f"{video_info.get('Width', 'N/A')} x {video_info.get('Height', 'N/A')}"
                video_details = [resolution]
                if video_info:
                    bitrate = video_info.get("BitRate_String", "N/A")
                    framerate = video_info.get("FrameRate_String", "N/A")
                    if bitrate != "N/A":
                        video_details.append(bitrate)
                    if framerate != "N/A":
                        video_details.append(framerate)
                    video_details.append(video_encode)
                video_details_str = " / ".join(video_details) if video_details else "N/A"

                audio_details = []
                if audio_info:
                    audio = meta.get("audio", "N/A")
                    bitrate = audio_info.get("BitRate_String", "N/A")
                    audio_details.extend([audio, bitrate]) if bitrate != "N/A" else [audio]
                audio_details_str = " @ ".join(audio_details) if audio_details else "N/A"

                size_bytes = int(mi[0].get("FileSize", 0)) if mi else 0
                size = (
                    f"{size_bytes} Bytes"
                    if size_bytes < 1024 * 1024
                    else f"{size_bytes / (1024 * 1024):.2f} MB"
                    if size_bytes < 1024 * 1024 * 1024
                    else f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
                )

                imdb_id = meta.get("imdb_id")
                category = meta["category"].lower()
                tmdb_id = meta.get("tmdb")
                plot = meta.get("overview", "") or meta["imdb_info"].get("plot", "")

                if self.config["DEFAULT"].get("add_logo", False) and meta.get("logo"):
                    await desc.write(f"[align=center][img]{meta['logo']}[/img][/align]\n")

                await desc.write(
                    f"[code]Filename........: {filename}\n"
                    f"Duration........: {duration}\n"
                    f"Video...........: {video_details_str}\n"
                    f"Audio...........: {audio_details_str}\n"
                    f"Subtitles.......: {subtitles}\n"
                    f"Size............: {size}\n"
                    f"IMDB............:[/code] [url]https://www.imdb.com/title/tt{imdb_id}/[/url]\n"
                    f"[code]TMDB............:[/code] [url]https://www.themoviedb.org/{category}/{tmdb_id}[/url]\n"
                    f"[code]Plot............: {plot}[/code]\n\n"
                )

                if meta.get("bdinfo") is not None:
                    bd_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "BD_SUMMARY_00.txt"
                    async with aiofiles.open(bd_path, "r", encoding="utf-8") as bd_file:
                        bd_dump = await bd_file.read()
                    await desc.write(f"[hide=BD iNFO]{bd_dump}[/hide]\n\n")
                else:
                    mi_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "MEDIAINFO.txt"
                    async with aiofiles.open(mi_path, "r", encoding="utf-8") as mi_file:
                        mi_dump = (await mi_file.read())[:-65].strip()
                    await desc.write(f"[hide=Mediainfo]{mi_dump}[/hide]\n\n")

                images = meta.get("image_list", [])
                if images:
                    await desc.write("[hide=Screenshots][align=center]\n")
                    for each in images:
                        await desc.write(f"[img]{each['raw_url']}[/img] ")
                    await desc.write("[/align][/hide]\n")

                await desc.write(f"\n\n{base}")

    async def get_cat_id(self, meta: Dict, for_search: bool = False) -> str:
        category_name = meta.get("category")
        resolution = meta.get("resolution", "")
        anime = meta.get("anime", False)
        sd = meta.get("sd", False)
        is_disc = meta.get("is_disc", False)
        tv_pack = meta.get("tv_pack", False)

        categories = {"MOVIE": "8" if for_search else "7", "TV": "1" if for_search else "0", "ANIME": "16", "MUSIC": "28"}
        category_id = categories.get(category_name, "0")

        if category_name in ("MOVIE", "TV"):
            if anime:
                category_id = "16" if sd else "17" if for_search else "16" if sd else "15"
            elif sd:
                category_id = "8" if category_name == "MOVIE" else "5" if tv_pack and for_search else "1"
            elif is_disc:
                category_id = "15" if for_search else "14"
            elif resolution in ("8640p", "4320p", "2160p"):
                if category_name == "MOVIE":
                    category_id = "10" if for_search else "9"
                else:
                    category_id = "7" if tv_pack and for_search else "6" if not for_search and tv_pack else "3" if for_search else "2"
            else:
                category_id = (
                    "6"
                    if category_name == "TV" and tv_pack and for_search
                    else "2"
                    if category_name == "TV"
                    else "9"
                    if for_search
                    else "8"
                )

        return category_id

    def get_language_tag(self, meta: Dict) -> str:
        lang_tag = ""
        has_eng_audio = False
        audio_lang = ""

        if meta["is_disc"] != "BDMV":
            try:
                mi_path = Path(meta.get("base_dir")) / "tmp" / meta.get("uuid") / "MediaInfo.json"
                with mi_path.open("r", encoding="utf-8") as f:
                    mi = json.load(f)
                for track in mi["media"]["track"]:
                    if track["@type"] == "Audio":
                        if track.get("Language", "None").startswith("en"):
                            has_eng_audio = True
                        elif not has_eng_audio:
                            audio_lang = mi["media"]["track"][2].get("Language_String", "").upper()
            except Exception as e:
                log.error(f"Error getting language tag: {e}")
        else:
            for audio in meta["bdinfo"]["audio"]:
                if audio["language"] == "English":
                    has_eng_audio = True
                elif not has_eng_audio:
                    audio_lang = meta["bdinfo"]["audio"][0]["language"].upper()

        return audio_lang if audio_lang else lang_tag

    def get_basename(self, meta: Dict) -> str:
        path = next(iter(meta["filelist"]), meta["path"])
        return Path(path).name

    async def get_name(self, meta: Dict) -> str:  # TODO change to edit_name
        manual_name = meta.get("manual_name")
        if manual_name:
            return manual_name

        media_type = meta.get("type", "")
        title = meta.get("title", "")
        artist = meta.get("artist", "")
        album = meta.get("album", "")
        year = meta.get("year", "")
        resolution = meta.get("resolution", "") if meta.get("resolution", "") != "OTHER" else ""
        audio = meta.get("audio", "")
        bit_depth = meta.get("bit_depth", "")
        bitrate = meta.get("bitrate", "")
        lang_tag = self.get_language_tag(meta)
        service = meta.get("service", "")
        season = "" if meta.get("no_season", False) else meta.get("season", "")
        episode = meta.get("episode", "")
        part = meta.get("part", "")
        repack = meta.get("repack", "")
        three_d = meta.get("3D", "")
        tag = meta.get("tag", "-NOGRP")
        source = meta.get("source", "")
        uhd = meta.get("uhd", "")
        hdr = meta.get("hdr", "")
        episode_title = meta.get("episode_title", "")

        if meta.get("is_disc", "") == "BDMV":
            video_codec = meta.get("video_codec", "")
            region = meta.get("region", "")
        elif meta.get("is_disc", "") == "DVD":
            region = meta.get("region", "")
            dvd_size = meta.get("dvd_size", "")
        else:
            video_codec = meta.get("video_codec", "")
            video_encode = meta.get("video_encode", video_codec)

        edition = meta.get("edition", "")
        cut = meta.get("cut", "")
        ratio = meta.get("ratio", "")

        if meta["category"] == "TV":
            year = meta.get("year", meta.get("imdb_info", {}).get("year", "")) if not meta.get("no_year", False) else ""

        if meta.get("debug"):
            log.debug(f"Category: {meta['category']}, Type: {media_type}")
            log.debug(f"Metadata: {meta}")

        naming_dict = {
            "MOVIE": {
                "DISC": {
                    "BDMV": f"{title} {year} {three_d} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {region} {uhd} {source} {hdr} {video_codec} {audio}{tag}",
                    "DVD": f"{title} {year} {lang_tag} {cut} {ratio} {edition} {repack} {source} {dvd_size} {audio}{tag}",
                    "HDDVD": f"{title} {year} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {source} {video_codec} {audio}{tag}",
                },
                "REMUX": {
                    (
                        "BluRay",
                        "HDDVD",
                    ): f"{title} {year} {three_d} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {audio}{tag}",
                    (
                        "PAL DVD",
                        "NTSC DVD",
                        "DVD",
                    ): f"{title} {year} {lang_tag} {cut} {ratio} {edition} {repack} {source} REMUX {audio}{tag}",
                },
                "ENCODE": f"{title} {year} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} {audio} {hdr} {video_encode}{tag}",
                "WEBDL": f"{title} {year} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEB-DL {audio} {hdr} {video_encode}{tag}",
                "WEBRIP": f"{title} {year} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEBRip {audio} {hdr} {video_encode}{tag}",
                "HDTV": f"{title} {year} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {source} {audio} {video_encode}{tag}",
            },
            "TV": {
                "DISC": {
                    "BDMV": f"{title} {year} {season}{episode} {three_d} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {region} {uhd} {source} {hdr} {video_codec} {audio}{tag}",
                    "DVD": f"{title} {season}{episode} {three_d} {lang_tag} {cut} {ratio} {edition} {repack} {source} {dvd_size} {audio}{tag}",
                    "HDDVD": f"{title} {year} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {source} {video_codec} {audio}{tag}",
                },
                "REMUX": {
                    (
                        "BluRay",
                        "HDDVD",
                    ): f"{title} {year} {season}{episode} {episode_title} {part} {three_d} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {audio}{tag}",
                    (
                        "PAL DVD",
                        "NTSC DVD",
                    ): f"{title} {year} {season}{episode} {episode_title} {part} {lang_tag} {cut} {ratio} {edition} {repack} {source} REMUX {audio}{tag}",
                },
                "ENCODE": f"{title} {year} {season}{episode} {episode_title} {part} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} {audio} {hdr} {video_encode}{tag}",
                "WEBDL": f"{title} {year} {season}{episode} {episode_title} {part} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEB-DL {audio} {hdr} {video_encode}{tag}",
                "WEBRIP": f"{title} {year} {season}{episode} {episode_title} {part} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEBRip {audio} {hdr} {video_encode}{tag}",
                "HDTV": f"{title} {year} {season}{episode} {episode_title} {part} {lang_tag} {cut} {ratio} {edition} {repack} {resolution} {source} {audio} {video_encode}{tag}",
            },
            "MUSIC": {
                (
                    "ALAC",
                    "FLAC",
                    "WAV",
                ): f"{artist} {album} {year} [{'edition'}] {source}{media_type} {bit_depth}bit {'' if tag == '-NOGRP' else tag}",
                "default": f"{artist} {album} {year} [{'edition'}] {source}{media_type} {bitrate} {'' if tag == '-NOGRP' else tag}",
            },
        }

        if meta["category"] == "MUSIC":
            edition = meta.get("manual_edition", edition)
            source = f"{source} " if source else ""
            name = naming_dict["MUSIC"][media_type if media_type in ("ALAC", "FLAC", "WAV") else "default"].format(edition=edition)
        else:
            if media_type == "DISC":
                name = naming_dict[meta["category"]][media_type][meta.get("is_disc", "BDMV")]
            elif media_type == "REMUX":
                name = naming_dict[meta["category"]][media_type][
                    tuple(k for k in naming_dict[meta["category"]][media_type] if source in k)[0]
                ]
            else:
                name = naming_dict[meta["category"]][media_type]

        name = name.replace(":", "").replace("'", "").replace("DD+", "DDP")
        return ".".join(part for part in name.split() if part)

    async def search_existing(self, meta: Dict) -> Dict[str, str]:
        await self.validate_credentials(meta)
        dupes = {}
        category_id = await self.get_cat_id(meta, for_search=True)
        title = urllib.parse.quote(meta.get("title", ""))
        search_url = (
            f"https://alpharatio.cc/torrents.php?action=basic&searchsubmit=1&"
            f"filter_cat%5B{category_id}%5D=1&searchstr={title}&order_by=time&order_way=desc"
        )

        if meta.get("debug", False):
            log.debug(f"Search URL: {search_url}")

        try:
            response = await self.session.get(search_url)
            response.raise_for_status()
            response_text = response.text

            if not self._has_existing_torrents(response_text):
                log.info("No existing torrents found or request failed.")
                return dupes

            soup = BeautifulSoup(response_text, "html.parser")
            torrent_rows = soup.find_all("tr", class_="torrent")
            log.debug(f"Found {len(torrent_rows)} torrent rows.")

            for row in torrent_rows:
                title_elem = row.find("a", title="View User Torrent") or row.find("a", title="View Auto Torrent")
                if title_elem:
                    name = title_elem.text.strip()
                    size = row.find_all("td")[4].text.strip()
                    dupes[name] = size
                else:
                    log.warning("No valid torrent name found in this row.")

            if not dupes:
                log.info("No duplicates found.")

        except httpx.RequestError as e:
            log.error(f"Search error: {e}")

        log.debug(f"Duplicates: {dupes}")
        return dupes

    def _has_existing_torrents(self, response_text: str) -> bool:
        return "Your search did not match anything." not in response_text

    def extract_auth_key(self, response_text: str) -> Optional[str]:
        soup = BeautifulSoup(response_text, "html.parser")
        logout_link = soup.find("a", href=True, text="Logout")
        if logout_link and (match := re.search(r"auth=([^&]+)", logout_link["href"])):
            return match.group(1)
        return None

    async def upload(self, meta: Dict):
        await edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)
        await self.edit_desc(meta)
        is_music = meta.get("is_music", False)

        desc_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]DESCRIPTION.txt"
        async with aiofiles.open(desc_path, "r", encoding="utf-8") as desc_file:
            desc = await desc_file.read()

        cover = (meta.get("poster") or meta["imdb_info"].get("cover")) if not is_music else meta.get("album_cover")
        while cover is None and not meta.get("unattended", False):
            cover = Prompt.ask("No Poster was found. Please input a link to a poster:", default="")
            if not re.match(r"https?://.*\.(jpg|png|gif)$", cover):
                log.warning("Invalid image link provided.")
                cover = None

        genres = meta.get("genres")
        tags = (
            f"tt{meta.get('imdb_id', '')}, {', '.join(tag.strip('.') for tag in (item.replace(' ', '.') for item in genres.split(',')))}"
            if genres and meta.get("imdb_id")
            else genres or ""
        ).replace("..", ".")

        initial_response = await self.get_initial_response()
        auth_key = self.extract_auth_key(initial_response)
        if not auth_key:
            raise Exception("Failed to extract auth key.")

        session_cookie = self.session.cookies.get("session")
        if not session_cookie:
            raise Exception("Session cookie not found.")

        ar_name = meta.get("manual_name") or await self.get_name(meta)
        data = {
            "submit": "true",
            "auth": auth_key,
            "type": await self.get_cat_id(meta),
            "title": ar_name,
            "tags": tags,
            "image": cover,
            "desc": desc,
        }

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Origin": "https://alpharatio.cc",
            "Referer": "https://alpharatio.cc/upload.php",
            "Cookie": f"session={session_cookie}",
        }

        torrent_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]{meta['clean_name']}.torrent"
        if meta.get("debug", False):
            log.debug(f"Upload data: {data}")

        try:
            async with aiofiles.open(torrent_path, "rb") as torrent_file:
                files = {"file_input": (f"{meta['clean_name']}.torrent", await torrent_file.read(), "application/x-bittorrent")}
                if not meta.get("debug"):
                    response = await self.session.post(self.upload_url, data=data, files=files, headers=headers)
                    response.raise_for_status()
                    await self.update_torrent_file(torrent_path, str(response.url))
                else:
                    log.debug("Debug mode: Skipping actual upload.")
        except (FileNotFoundError, httpx.RequestError) as e:
            log.error(f"Upload failed: {e}")
            raise

    async def update_torrent_file(self, torrent_path: Path, comment: str):
        try:
            base_torrent = Torrent.read(str(torrent_path))
            base_torrent.comment = f"Created by Uploadrr for AlphaRatio: {comment}"
            base_torrent.write(str(torrent_path), overwrite=True)
            log.debug(f"Updated torrent file with comment: {comment}")
        except Exception as e:
            log.error(f"Error updating torrent file: {e}")
            raise
