from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class FNP(Unit3d):
    tracker = "FNP"
    source_flag = "FnP"
    domain = "https://fearnopeer.com"
    allowed_imghosts = []
    banned_groups = {  # Updated 25-08-28
        "ALL_TYPES": [
            "4K4U",
            "BiTOR",
            "d3g",
            "FGT",
            "FRDS",
            "FTUApps",
            "GalaxyRG",
            "LAMA",
            "MeGusta",
            "NeoNoir",
            "O(logN)",
            "PSA",
            "RARBG",
            "x0r",
            "YAWNiX",
            "YIFY",
            "YTS",
        ],
        "EVO": ["WEBRIP", "ENCODE", "DVDRIP"],
    }

    # fmt: off
    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        anime = meta.get('anime', False)
        sports = meta.get("is_sports", False)
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            "MUSIC": "3",
        }.get(category_name, "0")
        if anime:
            category_id = "6"
        elif sports:
            category_id = "9"
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
            "FLAC": "7",
            "MP3": "11"
        }.get(media_type, "0")
        return type_id
    # fmt: on
