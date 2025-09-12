from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class SHRI(Unit3d):
    tracker = "SHRI"
    source_flag = "ShareIsland"
    domain = "https://shareisland.org"
    allowed_imghosts = []
    banned_groups = [""]

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        category_id = {"MOVIE": "1", "TV": "2", "MUSIC": "3"}.get(category_name, "0")
        return category_id

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        type = meta["type"]
        type_id = {
            "DISC": "26",
            "REMUX": "7",
            "WEBDL": "27",
            "WEBRIP": "27",
            "HDTV": "33",
            "ENCODE": "15",
            "FLAC": "24",
            "ALAC": "47",
            "AC3": "47",
            "AAC": "47",
            "MP3": "25",
        }.get(type, "0")
        return type_id

    # TODO edit_name as per https://shareisland.org/pages/8#8
    async def banned_content(self, meta: Dict[str, Any]) -> tuple[bool, bool]:
        path = meta.get("uuid", "")
        adult = meta.get("adult", False)
        keywords = meta.get("keywords", "")
        keyword_list = [k.strip() for k in keywords.lower().split(",")]
        if meta.get("xxx") or (
            ("XXX" in path or any(keyword in keyword_list for keyword in ["porn", "hentai", "erotic", "animation", "sex"])) and adult
        ):
            console.print("[bold red]Porn has been banned from ShareIsland since 25-04-16")
            return True, True
        return False, False
