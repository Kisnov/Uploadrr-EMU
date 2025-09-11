import asyncio
from typing import Any, Dict

import httpx

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class LST(Unit3d):
    tracker = "LST"
    source_flag = "LST.GG"
    domain = "https://lst.gg"
    allowed_imghosts = []
    banned_groups = {  # Updated 25-09-10  == https://lst.gg/pages/blacklist/groups
        "ALL_TYPES": [
            "-ZR-",
            "4K4U",
            "Aisha",
            "Alcaide_Kira",
            "ARCADE",
            "aXXo",
            "B3LLUM",
            "BHDStudio",
            "BiTOR",
            "BONE",
            "BRrip",
            "CM8",
            "CrEwSaDe",
            "CTFOH",
            "d3g",
            "dAV1nci",
            "DepraveD",
            "DNL",
            "EVO",
            "FaNGDiNG0",
            "FGT",
            "FoxTorrentX",
            "GalaxyTV",
            "HD2DVD",
            "HDHub4u",
            "HDTime",
            "iHYTECH",
            "ION10",
            "iPlanet",
            "iVy",
            "KiNGDOM",
            "LAMA",
            "MeGusta",
            "mHD",
            "mSD",
            "NaNi",
            "NhaNc3",
            "nHD",
            "nikt0",
            "nSD",
            "OFT",
            "OldT",
            "OzONE",
            "PHOCiS",
            "PiGNUS",
            "PRODJi",
            "PSA",
            "RARBG",
            "Rifftrax",
            "SANTi",
            "SasukeducK",
            "seedpool",
            "SEEDSTER",
            "ShAaNiG",
            "Sicario",
            "SP3LL",
            "STUTTERSHIT",
            "TAoE",
            "TGALAXY",
            "TGx",
            "TORRENTGALAXY",
            "ToVaR",
            "TSP",
            "TSPxL",
            "UnKn0wn",
            "ViSION",
            "VXT",
            "WAF",
            "WKS",
            "x0r",
            "YIFY",
            "YTS",
            "YTS.MX",
        ],
        "EVO": ["REMUX", "WEBRIP", "ENCODE"],
    }

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        keywords = meta.get("keywords", "")
        adult = meta.get("adult", False)
        anime = meta.get("anime", False)
        keyword_list = [k.strip() for k in keywords.lower().split(",")]
        category_id = {"MOVIE": "1", "TV": "2", "MUSIC": "3"}.get(category_name, "0")
        if (adult and anime) or "hentai" in keyword_list or (adult and "animation" in keyword_list):
            category_id = "8"
        return category_id

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
