from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class OTW(Unit3d):
    tracker = "OTW"
    source_flag = "OTW"
    domain = ""
    allowed_imghosts = []
    banned_groups = {
        "ALL_TYPES": [
            "[Oj]",
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
            "OFT",
            "nSD",
            "PiRaTeS",
            "playBD",
            "PlaySD",
            "playXD",
            "PRODJi",
            "RAPiDCOWS",
            "RARBG",
            "RetroPeeps",
            "RDN",
            "REsuRRecTioN",
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
        ]
    }

    async def edit_name(self, meta: Dict[str, Any]) -> str:
        otw_name = meta["name_notag"]
        if meta["category"] == "TV":
            try:
                year = meta["year"]
                if not year:
                    raise ValueError("No TMDB Year Found..trying IMDB")
            except (KeyError, ValueError):
                try:
                    year = meta["imdb_info"]["year"]
                    if not year:
                        raise ValueError("No IMDB Year Found..")
                except (KeyError, ValueError):
                    year = ""
            if meta.get("no_year", False) is False:
                otw_name = otw_name.replace(meta["title"], f"{meta['title']} {year}", 1)
        return otw_name

    async def banned_content(self, meta: Dict[str, Any]) -> tuple[bool, bool]:
        path = meta.get("uuid", "")
        adult = meta.get("adult", False)
        keywords = meta.get("keywords", "")
        keyword_list = [k.strip() for k in keywords.lower().split(",")]
        if meta.get("xxx") or (
            ("XXX" in path or any(keyword in keyword_list for keyword in ["porn", "hentai", "erotic", "animation", "sex"])) and adult
        ):
            console.print("[bold red]OldToons strictly prohibits Porn/Hentai.")
            return True, True
        elif adult:
            console.print("[bold red]Flagged as adult content. OldToons is family friendly. Please check rules.")
            return True, False
        return False, False
