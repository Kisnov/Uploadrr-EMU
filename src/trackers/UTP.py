from typing import Any, Dict

from .UNIT3D import Unit3d


class UTP(Unit3d):
    tracker = "UTP"
    source_flag = "UTOPIA"
    domain = "https://utp.to"
    banned_groups = [""]

    # fmt: off
    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        edition = meta.get("edition", "")
        category_id = {
            "MOVIE": "1",
            "TV": "2",
            "FANRES": "3"
        }.get(category_name, "0")
        if category_name == "MOVIE" and "FANRES" in edition:
            category_id = "3"
        return category_id

    async def get_res_id(self, meta: Dict[str, Any]) -> str:
        resolution = meta["resolution"]
        resolution_id = {
            "4320p": "1",
            "2160p": "2",
            "1080p": "3",
            "1080i": "4"
        }.get(resolution, "1")
        return resolution_id
    # fmt: on
    async def edit_name(self, meta: Dict[str, Any]) -> str:
        name = meta["name"]
        category = meta.get("category", "")
        media_type = meta.get("type", "")
        source = meta.get("source", "").upper()
        is_disc = meta.get("is_disc", "")
        title = meta.get("title", "")
        alt_title = meta.get("aka", "") if not meta.get("no_aka", False) else ""
        year = meta.get("year", "") if not meta.get("no_year", False) else ""
        resolution = meta.get("resolution", "")
        if resolution == "OTHER":
            resolution = ""
        audio = meta.get("audio", "")
        season = meta.get("season", "") if not meta.get("no_season", False) else ""
        episode = meta.get("episode", "")
        episode_title = meta.get("episode_title", "")
        tag = meta.get("tag", "")
        video_codec = meta.get("video_codec", "") if is_disc in ("BDMV", "HDDVD") or media_type in ("HDTV",) else ""

        if not tag and not name.upper().endswith(("-NOGRP", "-NOGROUP")):
            name = name + "-NOGRP"

        # Source renaming
        if media_type == "REMUX":
            if source == "BLURAY":
                name = name.replace("REMUX BluRay", "BDRemux") if "REMUX BluRay" in name else name.replace("REMUX", "BDRemux")
            elif source in ("PAL DVD", "NTSC DVD", "DVD"):
                name = name.replace("REMUX PAL DVD", "DVDRemux").replace("REMUX NTSC DVD", "DVDRemux").replace("REMUX DVD", "DVDRemux")
            elif source == "HDDVD":
                name = name.replace("REMUX HDDVD", "DVDRemux")
        elif media_type == "ENCODE":
            name = name.replace(f"{source}", "BDRip") if source in name else name.replace("ENCODE", "BDRip")

        # Reorder: insert alt_title after title if needed
        if alt_title and alt_title not in name:
            title_pos = name.find(title) + len(title)
            name = f"{name[:title_pos]} {alt_title}{name[title_pos:]}"

        # Adjust year for TV if needed
        if category == "TV" and year:
            # Ensure year is after title or season/episode based on type
            if media_type in ("DISC", "ENCODE", "HDTV") and year not in name:
                title_pos = name.find(title) + len(title)
                name = f"{name[:title_pos]} {year}{name[title_pos:]}"
            elif media_type in ("REMUX", "WEBDL", "WEBRIP") and year not in name:
                episode_pos = name.find(f"{season}{episode}") + len(f"{season}{episode}")
                name = f"{name[:episode_pos]} {year}{name[episode_pos:]}"

        # Add episode_title for TV if missing
        if category == "TV" and episode_title and media_type in ("REMUX", "ENCODE", "WEBDL", "WEBRIP", "HDTV"):
            if episode_title not in name:
                episode_pos = name.find(f"{season}{episode}") + len(f"{season}{episode}")
                name = f"{name[:episode_pos]} {episode_title}{name[episode_pos:]}"

        # Remove fields not needed
        if media_type == "REMUX" and source == "BLURAY":
            for field in [video_codec, audio]:
                if field in name:
                    name = name.replace(field, "").strip()
        elif media_type == "ENCODE":
            if audio in name:
                name = name.replace(audio, "").strip()

        name = name.replace(": ", " - ")
        return " ".join(name.split())
