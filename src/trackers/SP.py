from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class SP(Unit3d):
    tracker = "SP"
    source_flag = "seedpool.org"
    domain = "https://seedpool.org"
    allowed_imghosts = []
    banned_groups = []

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        anime = meta.get("anime", False)
        sports = meta.get("is_sport", False)
        category_id = {"MOVIE": "1", "TV": "2", "MUSIC": "5"}.get(category_name, "0")
        if anime:
            return "6"
        elif sports:
            return "8"
        return category_id

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        type = meta["type"]
        is_music = meta.get("is_music", False)
        edition = meta.get("edition", "")
        type_id_map = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "FLAC": "30",
            "MP3": "31",
            "UPSCALE": "14",
        }
        type_id = type_id_map.get(type, "0")
        if type_id == "0":
            return "17"
        if is_music and type not in ("FLAC", "MP3"):
            return "29"
        if edition.upper() in ("AI UPSCALE", "UPSCALE"):
            return "14"
        return type_id
