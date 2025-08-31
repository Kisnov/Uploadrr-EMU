import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Tuple

import aiofiles
from rich.prompt import Confirm

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
                        console.print(f"[bold yellow]This encode is bitstarved ({bitrate_str}). Upload anyway? [y/N]")
                        if not Confirm.ask("Upload anyway?", default=False):
                            return False, True  # user said no, so skip
                        return False, False  # user allowed upload
                break

        return False, False

    async def get_extra_data(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        return {"plex_friendly": await self.is_plex_friendly(meta)}

    def get_basename(self, meta):
        path = next(iter(meta["filelist"]), meta["path"])
        return os.path.basename(path)

    async def get_typeid(self, meta: Dict[str, Any]) -> str:
        basename = self.get_basename(meta)
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
        basename = self.get_basename(meta)  # For Hybrid/DS4K/RM4K checks
        category = meta["category"]
        type = meta["type"]
        source = meta.get("source", "")
        is_disc = meta.get("is_disc", "") if type == "DISC" else ""
        resolution = meta.get("resolution", "")
        uhd_str = "UHD " if meta.get("uhd", False) else ""
        _3d_str = "3D " if meta.get("3d", False) else ""
        hc = meta.get("hardcoded_subs", False)
        repack = meta.get("repack", "")
        year = meta.get("year", "")
        search_year = meta.get("search_year", year) if category == "TV" else year

        # Load MediaInfo for additional details || TODO use mi singleton
        json_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "MediaInfo.json"
        try:
            async with aiofiles.open(json_path, "r", encoding="utf-8") as f:
                mi = json.loads(await f.read())
        except Exception:
            console.print("[bold red]Failed to load MediaInfo.json for name editing. Using defaults.[/bold red]")
            mi = {"media": {"track": []}}

        video_track = next((t for t in mi["media"]["track"] if t["@type"] == "Video"), {})
        has_encode_settings = "Encoded_Library_Settings" in video_track
        framerate = float(video_track.get("FrameRate", "0"))
        hfr = "HFR" if framerate > 30 else ""

        # Adjust audio to include language if appropriate
        lang = meta.get("lang", "")
        audio = meta.get("audio", "DD 2.0")
        if lang not in ("DUAL", "MULTI", "DUBBED"):
            audio += f" {lang.capitalize()}"

        # Year adjustment for hardcoded subs
        year_part = f"({search_year} Ensubbed)" if hc else f"({search_year})"

        # Title and pre-metadata (edition, season/episode for TV, etc.)
        title = meta["title"]
        edition = meta.get("edition", "")
        cut = meta.get("cut", "")
        ratio = meta.get("ratio", "")
        pre_parts = [cut, ratio, edition]
        pre_metadata = " ".join(filter(None, pre_parts))

        if category == "TV":
            season_int = meta.get("season_int", 0)
            episode_int = meta.get("episode_int", 0)
            season = f"S{season_int:02d}" if season_int else ""
            episode = f"E{episode_int:02d}" if episode_int else ""
            season_episode = season + episode if episode else season
            episode_title = meta.get("episode_title", "") if episode else ""
            part = meta.get("part", "")
            tv_pre_parts = [season_episode, episode_title, part, pre_metadata]
            pre_metadata = " ".join(filter(None, tv_pre_parts))

        huno_name = f"{title} {year_part} {pre_metadata}".strip()

        # Build metadata components in order
        metadata_components = []
        metadata_components.append(resolution)

        # SCL
        hybrid = "Hybrid" if "HYBRID" in basename.upper() else ""
        scale = "DS4K" if "DS4K" in basename.upper() else "RM4K" if "RM4K" in basename.upper() else ""
        if scale:
            metadata_components.append(scale)

        # SRV
        service = meta.get("service", "")
        if service and type in ("WEBDL", "WEBRIP"):
            metadata_components.append(service)

        # RGN
        region = meta.get("region", "")
        if region and type in ("DISC", "REMUX"):
            metadata_components.append(region)

        # SRC
        src = ""
        if type == "DISC":
            if is_disc == "BDMV":
                src = f"{_3d_str}{uhd_str}BluRay"
            elif is_disc == "DVD":
                src = f"{source} {meta.get('dvd_size', '')}".strip() if meta.get("dvd_size") else source
            elif is_disc == "HDDVD":
                src = "HD-DVD"
            else:
                src = source
        elif type == "REMUX":
            if "DVD" in source:
                src = "DVD REMUX"
            elif "HDDVD" in source:
                src = "HD-DVD REMUX"
            else:
                src = f"{_3d_str}{uhd_str}BluRay REMUX"
        elif type == "ENCODE" or type == "HDTV":
            src = f"{_3d_str}{uhd_str}{source}".strip()
        elif type == "WEBDL":
            src = "WEB-DL"
        elif type == "WEBRIP":
            src = "WEBRip"
        if src:
            metadata_components.append(src)

        # HYB
        if hybrid:
            metadata_components.append(hybrid)

        # PRO
        video_codec = meta["video_codec"]
        pro = ""
        if type in ("DISC", "REMUX"):
            pro = video_codec
        elif type in ("ENCODE", "WEBRIP") or has_encode_settings:
            pro = "x265" if video_codec == "HEVC" else "x264" if video_codec == "AVC" else video_codec.lower()
        else:  # WEBDL, WEBRIP, HDTV without settings
            pro = "H265" if video_codec == "HEVC" else "H264" if video_codec == "AVC" else video_codec
        # Skip PRO for DVD/HDDVD REMUX
        if type == "REMUX" and ("DVD" in source or "HDDVD" in source):
            pro = ""
        if pro:
            metadata_components.append(pro)

        # VFT
        hdr = meta.get("hdr", "SDR")
        if hdr != "SDR":
            metadata_components.append(hdr)

        # HFR
        if hfr:
            metadata_components.append(hfr)

        # Audio
        metadata_components.append(audio)

        # Tag
        tag = meta.get("tag", "")
        if tag:
            tag_with_space = tag.replace("-", " - ")
        else:
            tag_with_space = " -NOGRP"
        metadata_components.append(tag_with_space)

        metadata_segment = " ".join(filter(None, metadata_components))
        if metadata_segment:
            huno_name = f"{huno_name} ({metadata_segment})"
        if repack:
            huno_name += f" [{repack}]"

        huno_name = huno_name.replace(": ", "- ")
        huno_name = " ".join(huno_name.split())
        meta["huno_name"] = huno_name
        return huno_name
