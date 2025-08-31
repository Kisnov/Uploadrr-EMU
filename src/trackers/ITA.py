from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class ITA(Unit3d):
    tracker = "ITA"
    source_flag = "ItaTorrents"
    domain = "https://itatorrents.xyz"
    allowed_imghosts = []
    banned_groups = [""]

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        type_id = {
            "DISC": "1",
            "REMUX": "2",
            "WEBDL": "4",
            "WEBRIP": "5",
            "HDTV": "6",
            "ENCODE": "3",
            "FLAC": "7",
            "ALAC": "8",
            "AC3": "9",
            "AAC": "10",
            "MP3": "11",
        }.get(type, "0")
        return type_id
