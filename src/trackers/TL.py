import contextlib
import platform
import re
from pathlib import Path

import aiofiles
import httpx
from rich.pretty import Pretty

from src.trackers.COMMON import COMMON
from src.utils.console import console, log
from src.utils.torrentman import edit_torrent


class TL:
    CATEGORIES = {
        "Anime": 34,
        "Movie4K": 47,
        "MovieBluray": 13,
        "MovieBlurayRip": 14,
        "MovieCam": 8,
        "MovieTS": 9,
        "MovieDocumentary": 29,
        "MovieDvd": 12,
        "MovieDvdRip": 11,
        "MovieForeign": 36,
        "MovieHdRip": 43,
        "MovieWebrip": 37,
        "TvBoxsets": 27,
        "TvEpisodes": 26,
        "TvEpisodesHd": 32,
        "TvForeign": 44,
    }

    def __init__(self, config):
        self.config = config
        self.type = "other"
        self.tracker = "TL"
        self.source_flag = "TorrentLeech.org"
        self.upload_url = "https://www.torrentleech.org/torrents/upload/apiupload"
        self.banned_groups = [""]
        announce = self.config["TRACKERS"][self.tracker]["announce_key"]
        if announce.startswith("https://"):
            self.announce_url = announce
            self.announce_key = announce.split("/a/")[1].split("/announce")[0]
        else:
            self.announce_url = f"https://tracker.torrentleech.org/a/{announce}/announce"
            self.announce_key = announce
        self.timeout = httpx.Timeout(60.0)

    async def get_cat_id(self, common, meta):
        res = meta.get("resolution")
        if meta.get("anime", 0):
            return self.CATEGORIES["Anime"]
        if meta["category"] == "MOVIE":
            if meta["original_language"] != "en":
                return self.CATEGORIES["MovieForeign"]
            elif "Documentary" in meta["genres"]:
                return self.CATEGORIES["MovieDocumentary"]
            elif meta["uhd"] and res in ("1440p", "2160p", "4320p", "8640p"):
                return self.CATEGORIES["Movie4K"]
            elif meta["is_disc"] in ("BDMV", "HDDVD") or (meta["type"] == "REMUX" and meta["source"] in ("BluRay", "HDDVD")):
                return self.CATEGORIES["MovieBluray"]
            elif meta["type"] == "ENCODE" and meta["source"] in ("BluRay", "HDDVD"):
                return self.CATEGORIES["MovieBlurayRip"]
            elif meta["is_disc"] == "DVD" or (meta["type"] == "REMUX" and "DVD" in meta["source"]):
                return self.CATEGORIES["MovieDvd"]
            elif meta["type"] == "ENCODE" and "DVD" in meta["source"]:
                return self.CATEGORIES["MovieDvdRip"]
            elif "WEB" in meta["type"] and "2160" in meta["resolution"]:
                return self.CATEGORIES["Movie4K"]
            elif "WEB" in meta["type"]:
                return self.CATEGORIES["MovieWebrip"]
            elif meta["type"] == "HDTV":
                return self.CATEGORIES["MovieHdRip"]
        elif meta["category"] == "TV":
            if meta["original_language"] != "en":
                return self.CATEGORIES["TvForeign"]
            elif meta.get("tv_pack", 0):
                return self.CATEGORIES["TvBoxsets"]
            elif meta["sd"]:
                return self.CATEGORIES["TvEpisodes"]
            else:
                return self.CATEGORIES["TvEpisodesHd"]
        raise NotImplementedError("Failed to determine TL category!")

    async def upload(self, meta):
        common = COMMON(config=self.config)
        await edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)
        cat_id = await self.get_cat_id(common, meta)
        await common.unit3d_edit_desc(meta, self.tracker)

        desc_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]DESCRIPTION.txt"
        info_filename = "BD_SUMMARY_00" if meta["bdinfo"] is not None else "MEDIAINFO"
        info_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"{info_filename}.txt"
        torrent_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{self.tracker}]{meta['clean_name']}.torrent"

        async with aiofiles.open(desc_path, "r", encoding="utf-8") as f:
            desc = await f.read()
        desc = desc.replace("[center]", "<center>").replace("[/center]", "</center>")
        desc = re.sub(r"\[spoiler=.*?\]", "[spoiler]", desc, flags=re.IGNORECASE)
        async with aiofiles.open(desc_path, "w", encoding="utf-8") as f:
            await f.write(desc)

        async with contextlib.AsyncExitStack() as stack:
            open_desc = await stack.enter_async_context(aiofiles.open(desc_path, "a+", encoding="utf-8"))
            open_info = await stack.enter_async_context(aiofiles.open(info_path, "r", encoding="utf-8"))

            await open_desc.write("\n\n")
            await open_desc.write(await open_info.read())

        with contextlib.ExitStack() as stack:
            torrent_file = stack.enter_context(open(torrent_path, "rb"))
            desc_file = stack.enter_context(open(desc_path, "rb"))

            files = {
                "torrent": (meta["name"], torrent_file, "application/x-bittorrent"),
                "nfo": ("nfo.nfo", desc_file, "application/octet-stream"),
            }

            data = {"announcekey": self.announce_key, "category": cat_id}
            headers = {"User-Agent": f"Uploadrr v2 ({platform.system()} {platform.release()})"}

            if meta.get("debug", False):
                log.debug("[magenta]DATA 2 SEND[/magenta]:")
                console.print(Pretty(data))
                return "DEBUG"

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(self.upload_url, files=files, data=data, headers=headers)
                if not response.text.isnumeric():
                    log.error(f"[red]{response.text}")
                    return False
                return True
