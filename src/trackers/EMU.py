from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class EMU(Unit3d):
    tracker = "EMU"
    source_flag = "eMuwarez"
    domain = "https://emu-warez.com"
    allowed_imghosts = []
    banned_groups = [""]

    def edit_name(self, meta: Dict[str, Any]) -> str:
        built_name = meta["name"]
        title = meta.get("title", "")
        aka = meta.get("aka", "")
        og_title = meta.get("original_title", "")

        if meta.get("original_language", "") in ("es", "spa"):
            emu_name = built_name.replace(title, "").replace(aka, og_title).strip()
        else:
            emu_name = built_name

        # Validate length
        while len(emu_name) > 255:
            emu_name = emu_name.replace(aka, "")
            if len(emu_name) <= 255:
                break

            resolution = meta.get("resolution", "")
            emu_name = emu_name.replace(resolution, "")

            if len(emu_name) <= 255:
                break
            type = meta.get("type", "")
            emu_name = emu_name.replace(type, "")
            if len(emu_name) <= 255:
                break
        return emu_name[:255]
