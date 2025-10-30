from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class HHD(Unit3d):
    tracker = "HHD"
    source_flag = "HHD"
    domain = "https://homiehelpdesk.net"
    allowed_imghosts = []
    banned_groups = {  # Updated 25-08-29
        "ALL_TYPES": [
            "aXXo",
            "BONE",
            "BRrip",
            "CM8",
            "CrEwSaDe",
            "CTFOH",
            "dAV1nci",
            "d3g",
            "DNL",
            "FaNGDiNG0",
            "GalaxyTV",
            "HD2DVD",
            "HDTime",
            "iHYTECH",
            "ION10",
            "iPlanet",
            "KiNGDOM",
            "LAMA",
            "MeGusta",
            "mHD",
            "mSD",
            "nHD",
            "NaNi",
            "NhaNc3",
            "nikt0",
            "nSD",
            "OFT",
            "PSA",
            "PRODJi",
            "RARBG",
            "Rifftrax",
            "SANTi",
            "SasukeducK",
            "ShAaNiG",
            "Sicario",
            "STUTTERSHIT",
            "TGALAXY",
            "TORRENTGALAXY",
            "TSP",
            "TSPxL",
            "ViSION",
            "VXT",
            "WAF",
            "WKS",
            "x0r",
            "YAWNiX",
            "YIFY",
            "YTS",
        ],
        "EVO": ["ENCODE", "WEBRIP", "HDTV", "REMUX"],
        "HDT": ["REMUX"],
    }

    # fmt: off
    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        anime = meta.get("anime", False)
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            "Anime": "3",
            "MUSIC": "4",
            "XXX": "10"
        }.get(category_name, "0")
        if anime:
            return "3"
        return category_id

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        type = meta["type"]
        type_id = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "ENCODE": "3",
            "HDTV": "6",
            "FLAC": "7",
            "AAC": "8",
            "ALAC": "9",
            "M4A": "10",
            "M4B": "11",
            "MP4": "12",
            "MP3": "13",
            "Other": "7",
        }.get(type, "0")
        if type in ("OPUS", "WAVE", "MPEG4", "OGG", "AC3"):
            type_id = "7"
        return type_id
    # fmt: on
