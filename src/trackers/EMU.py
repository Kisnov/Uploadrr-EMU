from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class EMU(Unit3d):
    tracker = "EMU"
    source_flag = "eMuwarez"
    domain = "https://emu-warez.com"
    allowed_imghosts = []
    banned_groups = [""]

    # Los del formulario de subida: el 7 es 540p (no 576i) y no hay 480i. El SD entrelazado va como el progresivo.
    resolution_ids = {
        "4320p": "1",
        "2160p": "2",
        "1440p": "3",
        "1080p": "3",
        "1080i": "4",
        "720p": "5",
        "576p": "6",
        "576i": "6",
        "540p": "7",
        "480p": "8",
        "480i": "8",
    }

    async def get_res_id(self, meta: Dict[str, Any]) -> str:
        return self.resolution_ids.get(meta["resolution"], "10")

    # También del formulario: el 7 es SD (en UNIT3D, FLAC).
    type_ids = {"DISC": "1", "REMUX": "2", "ENCODE": "3", "WEBDL": "4", "WEBRIP": "5", "HDTV": "6", "SD": "7"}

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        return self.type_ids.get(meta["type"], "0")

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
