from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class RHD(Unit3d):
    tracker = "RHD"
    source_flag = "RocketHD"
    domain = "https://rocket-hd.cc"
    allowed_imghosts = []
    banned_groups = [  # Updated 25-08-29 == https://rocket-hd.cc/pages/1
        "1XBET",
        "MEGA",
        "MTZ",
        "Whistler",
        "WOTT",
        "Taylor.D",
        "HELD",
        "FSX",
        "FuN",
        "MagicX",
        "w00t",
        "PaTroL",
        "BB",
        "266 ers",
        "GTF",
        "JellyfinPlex",
        "2BA",
    ]

    async def edit_name(self, meta: Dict[str, Any]) -> str:
        rhd_name = meta["name"]
        lang_tag = ""
        _known = {
            "4K",
            "UHD",
            "1080P",
            "720P",
            "HDR",
            "DV",
            "DOLBY.VISION",
            "HDR10",
            "HDR10+",
            "HLG",
            "REMUX",
            "BLURAY",
            "WEB-DL",
            "WEBDL",
            "WEB",
            "HDTV",
            "SD",
        }
        for track in meta.get("mediainfo", {}).get("media", {}).get("track", []):
            if track.get("@type") == "Audio":
                lang = track.get("Language", "").lower()
                title = track.get("Title", "").lower()
                if lang in ("de", "ger", "deu", "german") or "german" in title:
                    lang_tag = "GERMAN"
                    break
            if track.get("@type") == "Text":
                lang = track.get("Language", "").lower()
                title = track.get("Title", "").lower()
                if lang in ("de", "ger", "deu", "german") or "german" in title:
                    lang_tag = "GERMAN SUBBED"
                    break
        if meta.get("dual_audio"):
            lang_tag = "GERMAN DL"
        if meta.get("uhd"):
            lang_tag = lang_tag + " UHD" if lang_tag else "UHD"
        name_parts = [
            part
            for part in [
                meta.get("edition"),
                meta.get("hybrid") + " HYBRID" if meta.get("hybrid") else "",
                meta.get("source"),
                meta.get("uhd"),
            ]
            if part
        ]
        if lang_tag:
            name_parts = rhd_name.split()
            existing_lang_tags = {"GERMAN", "GERMAN DL", "GERMAN ML", "GERMAN SUBBED"}
            name_parts = [part for part in name_parts if part.upper() not in existing_lang_tags]
            insert_index = next((i for i, part in enumerate(name_parts) if part.upper() in _known), len(name_parts))
            name_parts.insert(insert_index, lang_tag)
            rhd_name = " ".join(name_parts)
        if lang_tag == "GERMAN DL":
            rhd_name = rhd_name.replace("Dual-Audio ", "")
        if not meta.get("tag") and not (rhd_name.endswith("-NOGRP") or rhd_name.endswith("-NOGROUP")):
            rhd_name += "-NOGRP"
            rhd_name = " ".join(rhd_name.split())
            meta["rhd_name"] = rhd_name
        return rhd_name
