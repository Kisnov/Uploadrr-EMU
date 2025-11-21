from typing import Any, Dict, Tuple

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class IHD(Unit3d):
    tracker = "IHD"
    source_flag = "InfinityHD"
    domain = "https://infinityhd.net"
    allowed_imghosts = []
    banned_groups = [""]

    async def banned_content(self, meta: Dict[str, Any]) -> Tuple[bool, bool]:
        if meta["category"] not in ("MOVIE", "TV"):
            console.print("Only Movie/TV/Anime Allowed on InfinityHD")
            return True, True
        return False, False

    # fmt: off
    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        anime = meta.get('anime')
        if anime:
            if category_name == "MOVIE":
                return "4"
            else:
                return '3'
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            'Anime': '3',
            'Anime Movie': '4'
        }.get(category_name, "0")
        return category_id
    # fmt: on
