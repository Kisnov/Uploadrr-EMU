import asyncio
import base64
import datetime
import re
from typing import Any, Dict

import httpx
from rich.pretty import Pretty

from src.trackers.COMMON import COMMON
from src.utils.console import console, log
from src.utils.torrentman import edit_torrent

from .UNIT3D import Unit3d


class RTF(Unit3d):
    def __init__(self, config):
        super().__init__(config)
        self.tracker = "RTF"
        self.source_flag = "sunshine"
        self.domain = "https://retroflix.club"
        self.upload_url = f"{self.domain}/api/upload"
        self.search_url = f"{self.domain}/api/torrent"
        self.forum_link = f"{self.domain}/forums.php?action=viewtopic&topicid=3619"
        self.allowed_imghosts = []
        self.banned_groups = []

    async def banned_content(self, meta: Dict[str, Any]) -> tuple[bool, bool]:
        path = meta.get("uuid", "")
        adult = meta.get("adult", False)
        keywords = meta.get("keywords", "")
        keyword_list = [k.strip() for k in keywords.lower().split(",")]
        if datetime.date.today().year - meta["year"] <= 9:
            console.print("[bold red]RetroFlix requires uploads to be more than 10 Years old!")
            return True, True
        elif meta.get("xxx") or (
            ("XXX" in path or any(keyword in keyword_list for keyword in ["porn", "hentai", "erotic", "animation", "sex"])) and adult
        ):
            console.print("[bold red]Porn is strictly prohibited on RetroFlix")
            return True, True
        elif adult:
            console.print("[bold red]Flagged as adult content.RetroFlix bans all porn/erotica.")
            return True, False
        return False, False

    async def upload(self, meta: Dict[str, Any]) -> bool:
        common = COMMON(config=self.config)
        await edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)
        await common.unit3d_edit_desc(meta, self.tracker, self.forum_link)
        if meta["bdinfo"] is not None:
            mi_dump = None
            bd_dump = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/BD_SUMMARY_00.txt", "r", encoding="utf-8").read()
        else:
            mi_dump = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt", "r", encoding="utf-8").read()
            bd_dump = None

        screenshots = []
        for image in meta["image_list"]:
            if image["raw_url"] is not None:
                screenshots.append(image["raw_url"])

        data = {
            "name": meta["name"],
            # description does not work for some reason
            # 'description' : meta['overview'] + "\n\n" + desc + "\n\n" + "Uploaded by Uploadrr",
            "description": "this is a description",
            # editing mediainfo so that instead of 1 080p its 1,080p as site mediainfo parser wont work other wise.
            "mediaInfo": re.sub(r"(\d+)\s+(\d+)", r"\1,\2", mi_dump) if bd_dump is None else f"{bd_dump}",
            "nfo": "",
            "url": "https://www.imdb.com/title/"
            + (meta["imdb_id"] if str(meta["imdb_id"]).startswith("tt") else "tt" + meta["imdb_id"])
            + "/",
            # auto pulled from IMDB
            "descr": "This is short description",
            "poster": meta["poster"] if meta["poster"] is not None else "",
            "type": "401" if meta["category"] == "MOVIE" else "402",
            "screenshots": screenshots,
            "isAnonymous": self.config["TRACKERS"][self.tracker]["anon"],
        }

        with open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}]{meta['clean_name']}.torrent", "rb") as binary_file:
            binary_file_data = binary_file.read()
            base64_encoded_data = base64.b64encode(binary_file_data)
            base64_message = base64_encoded_data.decode("utf-8")
            data["file"] = base64_message

        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
        }

        if meta.get("debug", False):
            log.debug("[magenta]DATA 2 SEND[/magenta]:")
            console.print(Pretty(data))
            return False

        success = "Unknown"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url=self.upload_url, json=data, headers=headers)
            response.raise_for_status()
            response_json = response.json()
            success = response_json.get("success", False)
            data = response_json.get("data", {})
        except Exception as e:
            console.print(f"[red]Encountered Error: {e}[/red]\n[bold yellow]May have uploaded, please go check..")
        if success == "Unknown":
            console.print("[bold yellow]Status of upload is unknown, please go check..")
            success = False
        elif success:
            console.print("[bold green]Torrent uploaded successfully!")
        else:
            console.print("[bold red]Torrent upload failed.")

        if data:
            if "name" in data and "The name has already been taken." in data["name"]:
                console.print("[red]Name has already been taken.")
            if "info_hash" in data and "The info hash has already been taken." in data["info_hash"]:
                console.print("[red]Info hash has already been taken.")
        else:
            console.print("[cyan]Request Data:")
            console.print(data)

        return success

    async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        dupes = {}
        console.print("[yellow]Searching for existing torrents on site...")
        headers = {"accept": "application/json", "Authorization": self.config["TRACKERS"][self.tracker]["api_key"].strip()}

        params = {"includingDead": "1"}

        # search is intentionally vague and just uses IMDB if available as many releases are not named properly on site.
        if meta["imdb_id"] != "0":
            params["imdbId"] = meta["imdb_id"] if str(meta["imdb_id"]).startswith("tt") else "tt" + meta["imdb_id"]
        else:
            params["search"] = meta["title"].replace(":", "").replace("'", "").replace(",", "")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(url=self.search_url, params=params, headers=headers)
                if response.status_code == 200:
                    response_json = response.json()
                    for each in response_json:
                        result = each["attributes"]["name"]
                        size = each["attributes"]["size"]
                        dupes[result] = size
                else:
                    console.print(f"[red]Failed to search: HTTP {response.status_code}[/red]")
            except Exception:
                console.print(
                    "[bold red]Unable to search for existing torrents on site. Either the site is down or your API key is incorrect"
                )
                await asyncio.sleep(5)

        return dupes
