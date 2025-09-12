from typing import Any, Dict, Tuple

from src.utils.console import console

from .UNIT3D import Unit3d


class ULCX(Unit3d):
    tracker = "ULCX"
    source_flag = "ULCX"
    domain = "https://upload.cx"
    banned_groups = {  # Updated 25-08-30 == https://upload.cx/wikis/6
        "ALL_TYPES": [
            "4K4U",
            "Alcaide_Kira",
            "AROMA",
            "d3g",
            "EMBER",
            "FGT",
            "FnP",
            "FRDS",
            "Grym",
            "Hi10",
            "iAHD",
            "INFINITY",
            "ION10",
            "iVy",
            "Judas",
            "LAMA",
            "MeGusta",
            "NAHOM",
            "Niblets",
            "nikt0",
            "OFT",
            "QxR",
            "RARBG",
            "Sicario",
            "SM737",
            "SPDVD",
            "SWTYBLZ",
            "TAoE",
            "TGx",
            "Tigole",
            "TSP",
            "TSPxL",
            "VXT",
            "Vyndros",
            "Will1869",
            "x0r",
            "YIFY",
        ],
        "EDGE2020": ["ENCODE", "WEBRIP"],
        "NuBz": ["ENCODE", "WEBRIP"],
        "Ralphy": ["ENCODE", "WEBRIP"],
    }

    async def banned_content(self, meta: Dict[str, Any]) -> Tuple[bool, bool]:
        path = meta.get("uuid", "")
        adult = meta.get("adult", False)
        keywords = meta.get("keywords", "")
        hdr = meta.get("hdr", "")
        encode = meta.get("video_encode", "")
        edition = meta.get("edition", "")
        uuid = meta["uuid"]
        keyword_list = [k.strip() for k in keywords.lower().split(",")]
        if meta.get("xxx") or (
            ("XXX" in path or any(keyword in keyword_list for keyword in ["porn", "hentai", "erotic", "animation", "sex"])) and adult
        ):
            console.print("[bold red]ULCX strictly prohibits Porn/Hentai.")
            return True, True
        elif adult:
            console.print("[bold red]Flagged as adult content. ULCX generally doesn't permit this content. Please check rules.")
            return True, False
        elif "HYBRID" in (edition or uuid) and hdr == "DV HDR":
            console.print("[bold red]ULCX prohibits HYBRID DV HDR movies.")
            return True, True
        elif encode == "x265" and hdr not in ("HDR", "DV", "DV HDR"):
            console.print("[bold red]ULCX prohibits x265 in non DV HDR movies.")
            return True, True
        return False, False

    async def edit_name(self, meta) -> str:
        ulcx_name = meta["name"]
        imdb_yr = meta["imdb_info"].get("year", 0)
        if imdb_yr != 0:
            ulcx_name = ulcx_name.replace(f"{meta['year']}", f"{imdb_yr}")
        return ulcx_name
