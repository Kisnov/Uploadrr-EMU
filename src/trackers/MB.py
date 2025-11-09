from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class MB(Unit3d):
    tracker = "MB"
    source_flag = "MalayaBits"
    domain = "https://malayabits.cc"
    allowed_imghosts = []
    banned_groups = [""]

    # fmt: off
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
            "MP3": "8"
        }.get(media_type, "0")
        return type_id
    # fmt: on
