import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple

import aiofiles
from rich.prompt import Confirm

from src.progress import ProgressManager
from src.trackers.UNIT3D import Unit3d
from src.utils.console import console


class HUNO(Unit3d):
    tracker = "HUNO"
    source_flag = "HUNO"
    domain = "https://hawke.uno"
    allowed_imghosts = []
    banned_groups = [  # Updated 25-08-29
        "4K4U",
        "Bearfish",
        "BiTOR",
        "BONE",
        "D3FiL3R",
        "d3g",
        "DTR",
        "ELiTE",
        "EVO",
        "EzzRips",
        "EzzTV",
        "FGT",
        "HashMiner",
        "HETeam",
        "HEVCBay",
        "HiQVE",
        "HR-DR",
        "iFT",
        "ION265",
        "iVy",
        "JATT",
        "Joy",
        "LAMA",
        "m3th",
        "MeGusta",
        "MRN",
        "Musafirboy",
        "OEPlus",
        "Pahe.in",
        "PHOCiS",
        "PoF",
        "PSA",
        "RARBG",
        "RMTeam",
        "ShieldBearer",
        "SiQ",
        "TBD",
        "Telly",
        "TSP",
        "VXT",
        "WKS",
        "YAWNiX",
        "YIFY",
        "YTS",
    ]

    async def banned_content(self, meta: Dict[str, Any]) -> Tuple[bool, bool]:
        pm = ProgressManager.get_instance()

        # Reject non-HEVC ENCODE/WEBRIP
        if meta.get("video_codec", "") != "HEVC" and meta["type"] in {"ENCODE", "WEBRIP"}:
            console.print("[bold red]Only x265/HEVC encodes are allowed")
            return True, True  # banned, and skip

        json_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "MediaInfo.json"
        try:
            async with aiofiles.open(json_path, "r", encoding="utf-8") as f:
                mi = json.loads(await f.read())
        except FileNotFoundError:
            console.print("[bold red]MediaInfo.json not found—cannot check bitrate. Assuming not banned.")
            return False, False
        except json.JSONDecodeError:
            console.print("[bold red]Invalid MediaInfo.json—cannot check bitrate. Assuming not banned.")
            return False, False

        for track in mi.get("media", {}).get("track", []):
            if track.get("@type") == "Video":
                bitrate = int(track.get("BitRate", 3000000))
                bitrate_str = track.get("BitRate_String", f"{bitrate // 1000} kbps")
                if bitrate < 3000000:
                    if meta.get("unattended"):
                        console.print(f"[bold yellow]Skipped due to low bitrate ({bitrate_str}).")
                        return False, True  # not banned, but skip
                    else:
                        console.print(f"[bold yellow]This encode is bitstarved ({bitrate_str}), should be above 3000 kb/s.")
                        with pm.pause_for_interaction():
                            if not Confirm.ask("Upload anyway?", default=False):
                                return False, True  # user said no, so skip
                        return False, False  # user allowed upload
                break

        return False, False

    async def get_extra_data(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        return {"stream": await self.is_plex_friendly(meta)}

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        basename = meta["uuid"]
        type = meta["type"]
        if type == "REMUX":
            return "2"
        elif type in ("WEBDL", "WEBRIP"):
            return "15" if "x265" in basename else "3"
        elif type in ("ENCODE", "HDTV"):
            return "15"
        elif type == "DISC":
            return "1"
        else:
            return "0"

    async def is_plex_friendly(self, meta: Dict[str, Any]) -> int:
        lossy_audio_codecs = ["AAC", "DD", "DDP", "OPUS"]
        if any(l in meta["audio"] for l in lossy_audio_codecs):
            return 1
        return 0

    async def edit_name(self, meta: Dict[str, Any]) -> str:  # NAMING GUIDE == https://hawke.uno/pages/4
        from src.name import NameGenerator

        template = {
            "MOVIE": "{title} ({year}) {distributor} {edition} {hc} ({resolution} {scale} {service} {3D} {uhd} {source} {profile} {hfr} {hdr} {audio} {lang} - {tag}) {repack}",
            "TV": "{title} ({year}) {season}{episode} {hc} ({resolution} {scale} {service} {3D} {uhd} {source} {profile} {hfr} {hdr} {audio} {lang} - {tag}) {repack}",
        }

        overrides = self._get_overrides(meta)

        meta["huno_name"] = await NameGenerator().generate_tracker_name(meta, self.tracker, template, overrides)

        return meta["huno_name"] or meta["name"]

    def _get_overrides(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        dvd_size = meta.get("dvd_size", "")
        hdr = meta.get("hdr")
        hc = meta.get("hardcoded_subs", False)
        is_disc = meta.get("is_disc", "")
        is_silent = meta.get("is_silent", False)
        lang = meta.get("lang", "")
        media_type = meta.get("type", "")
        repack = meta.get("repack")
        tag = meta.get("tag", "")
        vcode = meta.get("video_codec")
        vecode = meta.get("video_encode")

        uuid = meta["uuid"].upper()
        scale = "DS4K" if "DS4K" in uuid else "RM4K" if "RM4K" in uuid else ""

        source = meta.get("source", "")
        source = self._get_source(media_type, source, is_disc, dvd_size)

        profile = vecode.replace(".", "") if media_type in {"WEBDL", "WEBRIP", "ENCODE"} else vcode

        return {
            "hdr": hdr or "SDR",
            "hc": "Hardsubbed" if hc else "",
            "lang": "None" if is_silent else (lang.capitalize() if lang else ""),
            "profile": profile,
            "repack": f"[{repack}]" if repack else "",
            "scale": scale,
            "source": source,
            "tag": tag[1:] if tag else "",
        }

    def _get_source(self, media_type: str, source: str, is_disc: str, dvd_size: str) -> str:
        src = source or ""

        if media_type == "REMUX":
            if is_disc == "BDMV":
                return "BluRay REMUX"
            if is_disc == "DVD":
                dvd_part = dvd_size if dvd_size else "DVD"
                return f"{dvd_part} REMUX"
            return f"{src} REMUX" if src else "REMUX"

        if media_type in {"WEBDL", "WEBRIP"}:
            return "WEB-DL"

        if is_disc:
            if is_disc == "BDMV":
                return "BluRay"
            if is_disc == "DVD":
                return dvd_size.strip() if dvd_size else "DVD"

        return src
