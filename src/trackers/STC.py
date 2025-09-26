from typing import Any, Dict, Tuple

from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class STC(Unit3d):
    tracker = "STC"
    source_flag = "STC"
    domain = "https://skipthecommercials.xyz"
    allowed_imghosts = []
    banned_groups = [""]

    async def banned_content(self, meta: Dict[str, Any]) -> Tuple[bool, bool]:
        if meta["category"] not in ("TV", "Anime", "Movie"):
            console.print("[red]STC only allows TV Content!")
            return True, True
        if meta["category"] == "Movie":
            genkeys = set()
            for key in ("genres", "keywords"):
                val = meta.get(key)
                if isinstance(val, str):
                    genkeys.update(val.lower().split(", "))
                imdb_val = meta.get("imdb_info", {}).get(key)
                if isinstance(imdb_val, str):
                    genkeys.update(imdb_val.lower().split(", "))

            if "documentary" not in genkeys and "tv movie" not in genkeys:
                console.print("[red]STC Only Allows TV Movie/DOC, are you sure this is allowed?")
                return True, False
        return False, False
