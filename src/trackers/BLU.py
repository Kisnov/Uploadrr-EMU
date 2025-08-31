import asyncio
from typing import Any, Dict

import httpx
from rich.prompt import Confirm, Prompt

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class BLU(Unit3d):
    tracker = "BLU"
    source_flag = "BLU"
    domain = "https://blutopia.cc"
    allowed_imghosts = []
    banned_groups = {  # Checked 25-08-28
        "ALL_TYPES": [
            "3LTON",
            "4yEo",
            "ADE",
            "AFG",
            "AniHLS",
            "AnimeRG",
            "AniURL",
            "AROMA",
            "aXXo",
            "Brrip",
            "CHD",
            "CM8",
            "CrEwSaDe",
            "d3g",
            "DeadFish",
            "DNL",
            "ELiTE",
            "eSc",
            "FaNGDiNG0",
            "FGT",
            "Flights",
            "FRDS",
            "FUM",
            "HAiKU",
            "HD2DVD",
            "HDS",
            "HDTime",
            "Hi10",
            "ION10",
            "iPlanet",
            "JIVE",
            "KiNGDOM",
            "Leffe",
            "LEGi0N",
            "LOAD",
            "MeGusta",
            "mHD",
            "mSD",
            "NhaNc3",
            "nHD",
            "nikt0",
            "NOIVTC",
            "nSD",
            "PiRaTeS",
            "playBD",
            "PlaySD",
            "playXD",
            "PRODJi",
            "RAPiDCOWS",
            "RARBG",
            "RDN",
            "REsuRRecTioN",
            "RetroPeeps",
            "RMTeam",
            "SANTi",
            "SicFoI",
            "SPASM",
            "SPDVD",
            "STUTTERSHIT",
            "Telly",
            "TM",
            "TRiToN",
            "UPiNSMOKE",
            "URANiME",
            "WAF",
            "x0r",
            "xRed",
            "XS",
            "YIFY",
            "ZKBL",
            "ZmN",
            "ZMNT",
        ],
        "AOC": ["ENCODE", "HDTV", "WEBDL", "WEBRIP"],
        "CMRG": ["ENCODE", "WEBRIP"],
        "EVO": ["ENCODE", "WEBRIP"],
        "TERMiNAL": ["ENCODE", "WEBRIP"],
        "ViSION": ["ENCODE", "WEBRIP"],
    }

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        edition = meta.get("edition", "")
        category_id = {"MOVIE": "1", "TV": "2", "FANRES": "3"}.get(category_name, "0")
        if category_name == "MOVIE" and "FANRES" in edition:
            category_id = "3"
        return category_id

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        media_type = meta["type"]
        type_id = {"DISC": "1", "REMUX": "3", "WEBDL": "4", "WEBRIP": "5", "HDTV": "6", "ENCODE": "12"}.get(media_type, "0")
        return type_id

    async def derived_dv_layer(self, meta: Dict[str, Any]) -> tuple:
        name = meta["name"]
        desc_header = ""
        # Exit if not DV + HDR
        if not all([x in meta["hdr"] for x in ["HDR", "DV"]]):
            return name, desc_header
        console.print("[bold yellow]Generating the required description addition for Derived DV Layers. Please respond appropriately.")
        ask_comp = True
        if meta["type"] == "WEBDL":
            if Confirm.ask("Is the DV Layer sourced from the same service as the video?"):
                ask_comp = False
                desc_header = "[code]This release contains a derived Dolby Vision profile 8 layer. Comparisons not required as DV and HDR are from same provider.[/code]"

        if ask_comp:
            while desc_header == "":
                desc_input = Prompt.ask("Please provide comparisons between HDR masters. (link or bbcode)", default="")
                desc_header = f"[code]This release contains a derived Dolby Vision profile 8 layer. Comparisons between HDR masters: {desc_input}[/code]"

        if "hybrid" not in name.lower():
            if "REPACK" in name:
                name = name.replace("REPACK", "Hybrid REPACK")
            else:
                name = name.replace(meta["resolution"], f"Hybrid {meta['resolution']}")
        return name, desc_header

    async def upload(self, meta: Dict[str, Any]) -> bool:
        blu_name = meta["name"]
        desc_header = ""
        if meta.get("webdv", False):
            blu_name, desc_header = await self.derived_dv_layer(meta)
        meta["name"] = blu_name  # Temporarily set for base upload
        return await super().upload(meta)

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
