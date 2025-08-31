from typing import Any, Dict

from .UNIT3D import Unit3d


class TLZ(Unit3d):
    tracker = "TLZ"
    source_flag = "TLZ"
    domain = "https://tlzdigital.com"
    banned_groups = [""]

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        category_id = {"MOVIE": "1", "TV": "2", "MUSIC": "3"}.get(category_name, "0")
        return category_id

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        type_ = meta["type"]
        cat = meta["category"]
        tv_pack = meta.get("tv_pack", False)
        original_language = meta.get("original_language", "")
        try:
            if original_language != "en":
                type_id = "13"
            elif cat == "TV":
                if tv_pack:
                    type_id = "4"
                else:
                    type_id = "3"
            elif cat == "MOVIE":
                type_id = "1"
            elif type_ in ["MP3", "FLAC", "OTHER"]:
                type_id = {"MP3": "5", "FLAC": "6", "OTHER": "12"}.get(type_, "14")
            else:
                raise ValueError(f"Invalid type: {type_}")

        except ValueError:
            type_id = "12"

        return type_id
