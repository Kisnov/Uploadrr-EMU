import asyncio
import json
from typing import Any, Dict

import httpx

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class AITHER(Unit3d):
    tracker = "AITHER"
    source_flag = "Aither"
    domain = "https://aither.cc"
    allowed_imghosts = []
    banned_groups = {
        "ALL_TYPES": [
            "4K4U",
            "afm72",
            "AROMA",
            "Bandi",
            "BiTOR",
            "Bluespots",
            "BOLS",
            "d3g",
            "DepraveD",
            "EMBER",
            "FGT",
            "FreetheFish",
            "Garshasp",
            "Ghost",
            "Grym",
            "HDS",
            "Hi10",
            "HiQVE",
            "ImE",
            "ION10",
            "iVy",
            "Judas",
            "LAMA",
            "Langbard",
            "LION",
            "MeGusta",
            "MONOLITH",
            "Natty",
            "nikt0",
            "OEPlus",
            "OFT",
            "OsC",
            "Panda",
            "PYC",
            "QxR",
            "r00t",
            "Ralphy",
            "RARBG",
            "RetroPeeps",
            "RZeroX",
            "SAMPA",
            "Sicario",
            "SiCFoI",
            "Silence",
            "SkipTT",
            "SM737",
            "SPDVD",
            "STUTTERSHIT",
            "SWTYBLZ",
            "t3nzin",
            "TAoE",
            "TEKNO3D",
            "Telly",
            "TGx",
            "Tigole",
            "TSP",
            "TSPxL",
            "UnKn0wn",
            "VXT",
            "Vyndros",
            "Will1869",
            "x0r",
            "YIFY",
            "YTS",
            "YTS.MX",
        ],
        "Chivaman": ["ENCODE"],
        "CRUCiBLE": ["REMUX"],
        "edge2020": ["REMUX", "ENCODE"],
        "EVO": ["ENCODE"],
        "HDT": ["REMUX"],
        "noxxus": ["ENCODE"],
        "RCVR": ["ENCODE", "WEBRIP"],
        "Weasley[HONE]": ["WEBDL", "WEBRIP"],
    }

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        media_type = meta["type"]
        type_id = {"DISC": "1", "REMUX": "2", "WEBDL": "4", "WEBRIP": "5", "HDTV": "6", "ENCODE": "3", "DVDRIP": "3"}.get(
            media_type, "0"
        )
        return type_id

    async def edit_name(self, meta: Dict[str, Any]) -> str:
        name = meta["name"]
        has_eng_audio = False

        # Check audio language
        if meta["is_disc"] != "BDMV":
            with open(f"{meta.get('base_dir')}/tmp/{meta.get('uuid')}/MediaInfo.json", "r", encoding="utf-8") as f:
                mi = json.load(f)
            for track in mi["media"]["track"]:
                if track["@type"] == "Audio" and track.get("Language", "None").startswith("en"):
                    has_eng_audio = True
            if not has_eng_audio and mi["media"]["track"][2].get("Language_String"):
                audio_lang = mi["media"]["track"][2]["Language_String"].upper()
                name = name.replace(meta["resolution"], f"{audio_lang} {meta['resolution']}", 1)
        else:
            for audio in meta["bdinfo"]["audio"]:
                if audio["language"] == "English":
                    has_eng_audio = True
            if not has_eng_audio and meta["bdinfo"]["audio"]:
                audio_lang = meta["bdinfo"]["audio"][0]["language"].upper()
                name = name.replace(meta["resolution"], f"{audio_lang} {meta['resolution']}", 1)

        # DVD-specific adjustments
        if meta["is_disc"] == "DVD":
            name = name.replace(str(meta["year"]), f"{meta['year']} {meta['resolution']}", 1)
            name = name.replace(meta["audio"], f"{meta['video_codec']} {meta['audio']}", 1)

        # DVD Movie REMUX adjustments
        if meta["category"] == "MOVIE" and meta.get("type") == "REMUX" and meta.get("source", "") in ("PAL DVD", "NTSC DVD"):
            name = name.replace(meta["source"], f"{meta['resolution']} {meta['source']}", 1)
            name = name.replace("REMUX", f"REMUX {meta['video_codec']}", 1)

        # TV episode title adjustment
        if (
            meta["category"] == "TV"
            and meta.get("tv_pack", 0) == 0
            and meta.get("episode_title_storage", "").strip()
            and meta["episode"].strip()
        ):
            name = name.replace(meta["episode"], f"{meta['episode']} {meta['episode_title_storage']}", 1)

        return name

    async def search_existing(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        dupes: Dict[str, Any] = {}
        console.print(f"[yellow]Searching for existing torrents on {self.tracker}...")

        params = {
            "api_token": self.config["TRACKERS"][self.tracker]["api_key"].strip(),
            "tmdbId": meta["tmdb"],
            "categories[]": await self.get_cat_id(meta),
            "types[]": await self.get_type_id(meta),
            "resolutions[]": await self.get_res_id(meta),
            "name": "",
        }

        if meta["category"] == "TV":
            params["name"] += f" {meta.get('season', '')}{meta.get('episode', '')}"
        if meta.get("edition", ""):
            params["name"] += f" {meta['edition']}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            try:
                response = await client.get(self.search_url, params=params)
                if response.status_code == 200:
                    response_json = response.json()
                    for each in response_json.get("data", []):
                        name = each["attributes"]["name"]
                        size = each["attributes"]["size"]
                        dupes[name] = size
                else:
                    console.print(f"[red]Failed to search: HTTP {response.status_code}[/red]")
            except Exception as e:
                console.print(
                    f"[bold red]Unable to search for existing torrents on site. "
                    f"Either the site is down or your API key is incorrect. Error: {e}[/bold red]"
                )
                await asyncio.sleep(5)

        return dupes
