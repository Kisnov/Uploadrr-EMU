import inspect
import asyncio
import contextlib
import platform
import re
from pathlib import Path
from typing import Any, Dict, Tuple

import bs4
import httpx
from rich.pretty import Pretty

from src.utils.console import console, log
from src.utils.descgen import DescGenerator
from src.utils.torrentman import edit_torrent


class Unit3d:
    """
    Base class for Unit3D trackers.
    Subclasses must define:
    - tracker: str
    - source_flag: str
    - domain: str
    - allowed_imghosts: list[str]
    - banned_groups: list[str] or dict

    And implement/override as needed:
    - async def get_cat_id(self, meta: Dict[str, Any]) -> str
    - async def get_type_id(self, meta: Dict[str, Any]) -> str
    - async def get_res_id(self, meta: Dict[str, Any]) -> str
    - async def banned_content(self, meta: Dict[str, Any]) -> Tuple[bool, bool]
    - async def edit_name(self, meta: Dict[str, Any]) -> str (optional, for custom naming)
    - async def upload(self, meta: Dict[str, Any]) -> bool
    - async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.type = "unit3d"
        self.timeout = httpx.Timeout(45.0)
        self.upload_url = f"{self.domain}/api/torrents/upload"
        self.search_url = f"{self.domain}/api/torrents/filter"

    # fmt: off
    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            'MUSIC': '3'
        }.get(category_name, "0")
        return category_id

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        media_type = meta["type"]
        type_id = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "FLAC": "7"
        }.get(media_type, "0")
        return type_id

    async def get_res_id(self, meta: Dict[str, Any]) -> str:
        resolution = meta["resolution"]
        resolution_id = {
            "8640p": "10",
            "4320p": "1",
            "2160p": "2",
            "1440p": "3",
            "1080p": "3",
            "1080i": "4",
            "720p": "5",
            "576p": "6",
            "576i": "7",
            "480p": "8",
            "480i": "9",
        }.get(resolution, "10")
        return resolution_id
    # fmt: on
    async def banned_content(self, meta: Dict[str, Any]) -> Tuple[bool, bool]:
        """
        Check if content is banned.
        Returns (True, True) for immediate ban, (True, False) for warning, (False, False) for no ban.
        """
        return False, False

    async def get_extra_data(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Hook for extra data payload fields. Override in Trackers."""
        return {}

    async def get_extra_files(self, meta: Dict[str, Any]) -> Dict[str, Tuple[str, str, str]]:
        """
        Hook for extra file uploads. Override in subclasses for specific trackers.
        Return a dict where keys are form field names (e.g., 'cover', 'banner'),
        and values are tuples: (filename, path_to_file, mime_type).
        """
        return {}

    async def upload(self, meta: Dict[str, Any]) -> bool:
        descgen = DescGenerator(config=self.config, base_dir=meta["base_dir"])
        cat_id = await self.get_cat_id(meta)
        type_id = await self.get_type_id(meta)
        resolution_id = await self.get_res_id(meta)

        await descgen.edit_desc(meta, self.tracker)
        # region_id = await common.unit3d_region_ids(meta.get("region")) # TODO TEST THIS
        xanon = (
            1
            if (self.config["TRACKERS"][self.tracker].get("xanon", False) or self.config.get("ADULT", {}).get("always_anon", False))
            and meta.get("xxx")
            else 0
        )
        anon = (
            1
            if (meta.get("anon", 0) or self.config["TRACKERS"][self.tracker].get("anon", False))
            or self.config["DEFAULT"].get("always_anon", False)
            or xanon
            else 0
        )
        modq = 1 if meta.get("modq", False) or self.config["TRACKERS"][self.tracker].get("modq", False) else 0

        # Prepare file contents with context managers
        tmp_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
        if meta.get("bdinfo") is not None:
            mi_dump = None
            bd_dump = open(tmp_dir / "BD_SUMMARY_00.txt", "r", encoding="utf-8").read()
        else:
            mi_dump = open(tmp_dir / "MEDIAINFO.txt", "r", encoding="utf-8").read()
            bd_dump = None
        desc = open(tmp_dir / f"[{self.tracker}]DESCRIPTION.txt", "r", encoding="utf-8").read()
        torrent_path = tmp_dir / "BASE.torrent"
        nfo_file = meta.get("nfo_file")
        # cover = meta.get('album_cover', '')
        is_music = meta["is_music"]

        # Determine name: manual_name > edit_name (if method exists) > meta["name"]
        name = meta.get("manual_name")
        if not name and hasattr(self, "edit_name"):
            result = self.edit_name(meta)
            if inspect.isawaitable(result):
                name = await result
            else:
                name = result
        name = name or meta["name"]

        performers = meta.get("porndb", {}).get("performers", "") or ""
        tags = meta.get("porndb", {}).get("tags", "") or ""

        if performers and tags:
            adult_keywords = f"{performers}, {tags}"
        else:
            adult_keywords = performers or tags
        keywords = meta["keywords"]

        if meta["category"] in ["TV", "MOVIE"]:  # safety for now due to music/sports issue
            keywords = meta["keywords"].split(", ") if meta["keywords"] else []
            imdb_keywords = meta.get("imdb_info", {}).get("keywords", "").split(", ")
            keywords.extend(imdb_keywords)
            unique_keywords = list({kw.strip() for kw in keywords if kw.strip()})
            keywords = ", ".join(unique_keywords)

        # Build payload
        data = {
            "name": name,
            "description": desc,
            "mediainfo": mi_dump,
            "category_id": cat_id,
            "type_id": type_id,
            "tmdb": meta.get("tmdb", 0),
            "imdb": meta.get("imdb_id", 0),
            "tvdb": meta.get("tvdb_id", 0),
            "mal": meta.get("mal_id", 0),
            "igdb": 0,
            "anonymous": anon,
            "stream": meta["stream"],
            "sd": meta["sd"],
            "keywords": keywords or adult_keywords,
            "personal_release": int(meta.get("personalrelease", False)),
            "internal": 0,
            "featured": 0,
            "free": 0,
            "doubleup": 0,
            "sticky": 0,
            "mod_queue_opt_in": modq,
        }

        if not is_music:
            data.update({"resolution_id": resolution_id, "bdinfo": bd_dump})

        data.update(await self.get_extra_data(meta))

        # Internal
        if self.config["TRACKERS"][self.tracker].get("internal", False):
            if meta.get("tag", "") and meta["tag"][1:] in self.config["TRACKERS"][self.tracker].get("internal_groups", []):
                data["internal"] = "1"

        if meta.get("region_id", 0) != 0:
            data["region_id"] = meta["region_id"]
        if meta.get("distributor_id", 0) != 0:
            data["distributor_id"] = meta["distributor_id"]
        if meta.get("category") == "TV":
            data["season_number"] = int(meta.get("season_int", 0))
            data["episode_number"] = int(meta.get("episode_int", 0))

        headers = {"User-Agent": f"Uploadrr / v2.0 ({platform.system()} {platform.release()})"}
        params = {"api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip()}

        extra_files_meta = await self.get_extra_files(meta)

        if meta.get("debug", False):
            log.debug("[bright_magenta]DATA 2 SEND[/bright_magenta]:")
            console.print(Pretty(data))
            log.debug("[bright_magenta]FILES 2 SEND[/bright_magenta]:")
            files_info = {"torrent": torrent_path.name}
            if nfo_file:
                files_info["nfo"] = str(Path(nfo_file).name)
            for field, (fname, path, mime) in extra_files_meta.items():
                files_info[field] = str(Path(path).name)
            console.print(Pretty(files_info))
            await edit_torrent(meta, None, self.tracker, "DEBUG", "https://UNIT3D.CHANGES/INFOHASH/DEBUG-ONLY", "Uploadrr DEBUG-MODE")
            return "DEBUG"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            files = {}
            with contextlib.ExitStack() as stack:
                torrent_file = stack.enter_context(open(torrent_path, "rb"))
                files["torrent"] = ("torrent.torrent", torrent_file, "application/x-bittorrent")

                if nfo_file:
                    nfo_content = stack.enter_context(open(nfo_file, "rb"))
                    files["nfo"] = ("nfo.nfo", nfo_content, "application/octet-stream")

                # extra_files_meta
                for field, (fname, path, mime) in extra_files_meta.items():
                    f = stack.enter_context(open(path, "rb"))
                    files[field] = (fname, f, mime)

                try:
                    response = await client.post(
                        self.upload_url, data=data, files=files if files else None, headers=headers, params=params
                    )
                    response.raise_for_status()
                    if 200 <= response.status_code < 300:
                        try:
                            response_json = response.json()
                        except ValueError:
                            log.error(f"Unexpected response:\n{response.text}")
                            return False
                        success = response_json.get("success", False)
                        if success:
                            try:
                                raw_url = response_json.get("data", "")
                                if raw_url:
                                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                                        torrent_response = await client.get(raw_url)
                                        torrent_response.raise_for_status()
                                        torrent_data = torrent_response.content
                                        new_torrent = tmp_dir / f"[{self.tracker}]{meta['clean_name']}.torrent"
                                        new_torrent.write_bytes(torrent_data)
                                        url = raw_url.replace("/download", "s")
                                        url = re.sub(r"/(\d+)\..*$", r"/\1", url)
                                        comment = f"Made by Uploadrr for: {url}"
                                        await edit_torrent(torrent_path=new_torrent, comment=comment)
                                        console.print("[bold green]Torrent uploaded successfully!")
                            except httpx.HTTPStatusError as http_err:
                                console.print(
                                    f"[yellow]Torrent upload succeeded, but failed to download the torrent file: {http_err}[/yellow]"
                                )
                                return True
                            except Exception as e:
                                console.print(f"[red]Torrent upload succeeded, but failed to save the torrent file: {e}[/red]")
                                return True
                        else:
                            message = response_json.get("message", "No message provided")
                            console.print(f"[red]Upload failed: {message}[/red]")
                            if "data" in response_json:
                                console.print(f"[cyan]Error details:[/cyan] {response_json['data']}")
                        return success
                except httpx.HTTPStatusError as http_err:
                    if http_err.response.status_code in (404, 500):
                        soup = bs4.BeautifulSoup(http_err.response.text, "html.parser")
                        error_heading = soup.select_one("h1.error__heading")
                        error_body = soup.select_one("div.error__body")

                        if error_heading:
                            log.error(error_heading.text.strip())
                        if error_body:
                            log.error(error_body.text.strip())
                        if not error_heading and not error_body:
                            console.print(f"[blue]Raw Response:[/blue] {http_err.response.text}")
                        return False
                    else:
                        log.warning(f"[yellow]Unexpected Status Code ({http_err.response.status_code}):[/yellow]")
                        console.print(http_err.response.text)
                        return False
                except Exception:
                    console.print_exception()
                    return False

    async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        dupes: Dict[str, Any] = {}
        if self.tracker in ("LCD", "LT", "TTR"):
            console.print(f"[yellow]Buscando torrents existentes en {self.tracker}...")
        else:
            console.print(f"[yellow]Searching for existing torrents on {self.tracker}...")

        params = {
            "api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
            "tmdbId": meta.get("tmdb", 0),
            "categories[]": await self.get_cat_id(meta),
            "types[]": await self.get_type_id(meta),
            "resolutions[]": await self.get_res_id(meta),
            "name": meta.get("edition", ""),
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(self.search_url, params=params)
                if response.status_code == 200:
                    response_json = response.json()
                    for item in response_json.get("data", []):
                        name = item["attributes"]["name"]
                        size = item["attributes"]["size"]
                        dupes[name] = size
                else:
                    console.print(f"[red]Failed to search: HTTP {response.status_code}[/red]")
            except Exception as e:
                if self.tracker in ("LCD", "LT", "TTR"):
                    console.print(f"[red]No se pudo buscar torrents existentes: {e}[/red]")
                else:
                    console.print(f"[red]Unable to search for existing torrents: {e}[/red]")
                await asyncio.sleep(5)
        return dupes
