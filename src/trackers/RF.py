from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class RF(Unit3d):
    tracker = "RF"
    source_flag = "ReelFliX"
    domain = "https://reelflix.xyz"
    allowed_imghosts = []
    banned_groups = [""]  # 25-08-30 Confirmed with Slikk no BAN GROUP LIST (Retags are banned obv)

    async def banned_content(self, meta: Dict[str, Any]) -> tuple[bool, bool]:
        path = meta.get("uuid", "")
        adult = meta.get("adult", False)
        keywords = meta.get("keywords", "")
        keyword_list = [k.strip() for k in keywords.lower().split(",")]
        if meta["category"] != "MOVIE":
            console.print("[bold red]Content not Supported: ReelFLiX only support Movies.")
            return True, True
        elif ("XXX" in path or any(keyword in keyword_list for keyword in ["porn", "hentai", "erotic"])) and adult:
            console.print("[bold red]Porn is strictly prohibited on ReelFlix")
            return True, True
        elif adult:
            console.print("[bold red]Flagged as adult content. ReelFlix bans all porn/erotica.")
            return True, False
        return False, False

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        type = meta["type"]
        type_id = {"DISC": "43", "REMUX": "40", "WEBDL": "42", "WEBRIP": "45", "ENCODE": "41", "HDTV": "35"}.get(type, "0")
        return type_id
