import platform
import re
import urllib.parse
from pathlib import Path
from typing import Dict

import aiofiles
import httpx
import rich.prompt as Prompt
from bs4 import BeautifulSoup

from src.mediainfo import MediaInfoHandler
from src.utils.bbcode import BBCODE
from src.utils.console import console, log
from src.utils.cookieme import CookieMonster
from src.utils.descgen import DescGenerator
from src.utils.torrentman import edit_torrent


class AR:
    def __init__(self, config):
        self.config = config
        self.type = "other"  # TODO gazelle
        self.session = None
        self.tracker = "AR"
        self.source_flag = "AlphaRatio"
        self.username = config["TRACKERS"]["AR"].get("username", "").strip()
        self.password = config["TRACKERS"]["AR"].get("password", "").strip()
        pid = config["TRACKERS"][self.tracker].get("PID", "PASSKEY_HERE")
        if pid.startswith(("http://", "https://")):
            self.announce_url = pid
        else:
            self.announce_url = f"http://tracker.alpharatio.cc:2710/{pid}/announce"
        self.login_url = "https://alpharatio.cc/login.php"
        self.upload_url = "https://alpharatio.cc/upload.php"
        self.search_url = "https://alpharatio.cc/torrents.php"
        self.user_agent = f"Uploadrr ({platform.system()} {platform.release()})"
        self.cookie_file = None
        self.cookie_monster = None
        self.banned_groups = []
        self.auth_key = None

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
        cookie_path = Path(meta["base_dir"]) / "data" / "cookies" / f"{self.tracker}.pkl"
        self.cookie_monster = CookieMonster(cookie_path)

        if self.session is not None:
            log.warning("Previous session was not closed properly. Closing it now.")
            await self.close_session()

        await self.start_session()

        if await self.cookie_monster.load(self.session):

            def check_login(response: httpx.Response) -> bool:
                return "login.php?act=recover" not in response.text

            if await self.cookie_monster.validate(client=self.session, validate_url=self.search_url, check_func=check_login):
                log.info("Cookies loaded and validated successfully.")
                return True
            else:
                log.warning("Cookies loaded but validation failed. Attempting fresh login.")
        else:
            console.print("No valid cookie file found. Attempting login...")

        # No Valid Cookie -- Login
        if await self.login(meta):
            await self.cookie_monster.save(self.session)
            return True
        else:
            log.error("Login failed. Please check credentials and site status.")
            return False

    async def login(self, meta: Dict) -> bool:
        data = {"username": self.username, "password": self.password, "keeplogged": "1", "login": "Login"}
        try:
            response = await self.session.post(self.login_url, data=data, follow_redirects=True)
            response.raise_for_status()
            if "login.php?act=recover" in response.text:
                log.error("Login failed. Check your credentials.")
                return False
            return True
        except httpx.RequestError as e:
            log.error(f"Login failed: {e}")
            return False

    async def get_auth_key(self) -> str:
        if self.auth_key is not None:
            return self.auth_key

        try:
            response = await self.session.get(self.search_url)
            response.raise_for_status()
        except httpx.RequestError as e:
            log.error(f"Failed to fetch page for auth key: {e}")
            raise

        soup = BeautifulSoup(response.text, "html.parser")
        logout_link = soup.find("a", href=True, text="Logout")
        if logout_link is None:
            raise ValueError("Not logged in: No 'Logout' link found.")

        match = re.search(r"auth=([^&]+)", logout_link["href"])
        if match is None:
            raise ValueError("Auth key not found in logout link.")

        self.auth_key = match.group(1)
        return self.auth_key

    async def edit_desc(self, meta: Dict):
        bbcode = BBCODE()
        desc_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]DESCRIPTION.txt"
        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])

        if meta.get("is_music", False):
            desc = await descgen.create_music_description(meta, self.tracker)
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

                images = meta.get("image_list", {})
                if images:
                    await descgen._write_images(desc, meta, self.tracker, img_size=0)

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

    async def edit_name(self, meta: Dict) -> str:
        manual_name = meta.get("manual_name")
        if manual_name:
            return manual_name
        ar_name = meta["name"]
        ar_name = ar_name.replace(":", "").replace("'", "").replace(" ", ".")
        return ar_name

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

    def get_tags(self, meta: dict) -> str:
        def clean_tag(tag: str) -> str:
            return tag.strip().replace(" ", ".").strip(".")

        imdb_id = meta.get("imdb_id")
        tmdbg = meta.get("genres", "")
        imdbg = meta.get("imdb_info", {}).get("genres", "")

        seen = set()
        tags = []

        if imdb_id:
            imdb_tag = f"tt{imdb_id}"
            tags.append(imdb_tag)
            seen.add(imdb_tag)

        for tag in (tmdbg + "," + imdbg).split(","):
            cleaned = clean_tag(tag)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                tags.append(cleaned)

        return ", ".join(tags)

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

        auth_key = await self.get_auth_key()
        ar_name = meta.get("manual_name") or await self.edit_name(meta)
        data = {
            "submit": "true",
            "auth": auth_key,
            "type": await self.get_cat_id(meta),
            "title": ar_name,
            "tags": self.get_tags(meta),
            "image": cover,
            "desc": desc,
        }

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Origin": "https://alpharatio.cc",
            "Referer": "https://alpharatio.cc/upload.php",
        }

        torrent_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]{meta['clean_name']}.torrent"
        if meta.get("debug", False):
            log.debug(f"Upload data: {data}")

        try:
            async with aiofiles.open(torrent_path, "rb") as torrent_file:
                files = {"file_input": (f"{meta['clean_name']}.torrent", await torrent_file.read(), "application/x-bittorrent")}
                if not meta.get("debug"):
                    response = await self.session.post(self.upload_url, data=data, files=files, headers=headers, follow_redirects=True)
                    response.raise_for_status()
                    comment = f"Created by Uploadrr for AlphaRatio: {str(response.url)}"
                    await edit_torrent(torrent_path=torrent_path, comment=comment)
                else:
                    log.debug("Debug mode: Skipping actual upload.")
        except (FileNotFoundError, httpx.RequestError) as e:
            log.error(f"Upload failed: {e}")
            raise
