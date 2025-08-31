from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class LCD(Unit3d):
    tracker = "LCD"
    source_flag = "LOCADORA"
    domain = "https://locadora.cc"
    allowed_imghosts = []
    banned_groups = [""]

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        category_id = {"MOVIE": "1", "TV": "2", "ANIMES": "6"}.get(category_name, "0")
        if meta.get("anime", False) and category_id == "2":
            category_id = "6"
        return category_id

    async def edit_name(self, meta: Dict[str, Any]) -> str:
        name = (
            meta["uuid"]
            .replace(".mkv", "")
            .replace(".mp4", "")
            .replace(".", " ")
            .replace("DDP2 0", "DDP2.0")
            .replace("DDP5 1", "DDP5.1")
            .replace("H 264", "H.264")
            .replace("H 265", "H.264")
            .replace("DD+7 1", "DD+7.1")
            .replace("AAC2 0", "AAC2.0")
            .replace("DD5 1", "DD5.1")
            .replace("DD2 0", "DD2.0")
            .replace("TrueHD 7 1", "TrueHD 7.1")
            .replace("DTS-HD MA 7 1", "DTS-HD MA 7.1")
            .replace("-C A A", "-C.A.A")
        )
        return name
