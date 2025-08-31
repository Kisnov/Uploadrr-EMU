from typing import Any, Dict

import langcodes

from .UNIT3D import Unit3d


class TTR(Unit3d):
    tracker = "TTR"
    source_flag = "TTR"
    domain = "https://torrenteros.org"
    banned_groups = [""]

    # fmt: off
    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        category_id = {
            "MOVIE": "1",
            "TV": "2"
        }.get(category_name, "0")
        return category_id
    # fmt: on
    async def edit_name(self, meta: Dict[str, Any]) -> str:  # TODO switch to new method.
        # Extracting and cleaning up variables
        title = meta.get("title", "")
        year = meta.get("year", "")
        audio = meta.get("audio", "")
        source = meta.get("source", "")
        resolution = meta.get("resolution", "")
        edition = meta.get("edition", "")
        service = meta.get("service", "")
        video_codec = meta.get("video_codec", "")
        video_encode = meta.get("video_encode", "")
        three_d = meta.get("3d", "")
        hdr = meta.get("hdr", "")
        repack = meta.get("repack", "")
        cut = meta.get("cut", "")
        ratio = meta.get("ratio", "")
        tag = meta.get("tag", "")
        dvd_size = meta.get("dvd_size", "")
        season = meta.get("season", "")
        episode = meta.get("episode", "")
        part = meta.get("part", "")
        episode_title = meta.get("episode_title", "")
        aka = meta.get("aka", "")
        type = meta.get("type", "")
        category = meta.get("category", "")
        is_disc = meta.get("is_disc", "")
        uhd = "UHD " if "2160p" in resolution else ""
        region = f"Region {meta.get('region', '')}" if meta.get("region", "") else ""
        distributor = meta.get("distributor", "")
        alt_title = f"[{aka}]" if aka else ""
        lang = meta.get("original_language", "")
        lang_tag = f"[{langcodes.Language(lang).display_name(lang)}]" if lang != "en" else ""

        # Clean up tag and repack
        if tag.startswith("-"):
            tag = " " + tag
        if repack:
            repack = " " + repack

        # Clean up edition
        if edition:
            edition = edition.replace("/", " ").replace("Edition", "").strip()
            edition = " " + edition + " Edition"

        # Clean up source
        if source in ("PAL DVD", "NTSC DVD"):
            source = "DVD"

        # Build name based on category and type
        potential_missing = []
        name = ""
        if category == "MOVIE":  # MOVIE SPECIFIC
            if type == "DISC":  # Disk
                if is_disc == "BDMV":
                    name = f"{title} [{alt_title}] ({year}) {three_d} [{cut} {ratio} {edition} {repack} {resolution} {region} {uhd} {source} {hdr} {video_codec} {audio}{tag}] {lang_tag}"
                    potential_missing = ["edition", "region", "distributor"]
                elif is_disc == "DVD":
                    name = (
                        f"{title} {alt_title} ({year}) [{cut} {ratio} {edition} {repack} {source} {dvd_size} {audio}{tag}] {lang_tag}"
                    )
                    potential_missing = ["edition", "distributor"]
                elif is_disc == "HDDVD":
                    name = f"{title} {alt_title} ({year}) [{cut} {ratio} {edition} {repack} {resolution} {source} {video_codec}] {audio}{tag}] {lang_tag}"
                    potential_missing = ["edition", "region", "distributor"]
            elif type == "REMUX" and source in ("BluRay", "HDDVD"):  # BluRay/HDDVD Remux
                name = f"{title} {alt_title} ({year}) {three_d} [{cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {audio}{tag}] {lang_tag}"
                potential_missing = ["edition", "description"]
            elif type == "REMUX" and source in ("PAL DVD", "NTSC DVD", "DVD"):  # DVD Remux
                name = f"{title} {alt_title} ({year}) [{cut} {ratio} {edition} {repack} {source} REMUX  {audio}{tag}] {lang_tag}"
                potential_missing = ["edition", "description"]
            elif type == "ENCODE":  # Encode
                name = f"{title} {alt_title} ({year}) [{cut} {ratio} {edition} {repack} {resolution} {uhd} {source} {audio} {hdr} {video_encode}{tag}] {lang_tag}"
                potential_missing = ["edition", "description"]
            elif type == "WEBDL":  # WEB-DL
                name = f"{title} {alt_title} ({year}) [{cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEB-DL {audio} {hdr} {video_encode}{tag}] {lang_tag}"
                potential_missing = ["edition", "service"]
            elif type == "WEBRIP":  # WEBRip
                name = f"{title} {alt_title} ({year}) [{cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEBRip {audio} {hdr} {video_encode}{tag}] {lang_tag}"
                potential_missing = ["edition", "service"]
            elif type == "HDTV":  # HDTV
                name = f"{title} {alt_title} ({year}) [{cut} {ratio} {edition} {repack} {resolution} {source} {audio} {video_encode}{tag}] {lang_tag}"
                potential_missing = []
        elif category == "TV":  # TV SPECIFIC
            if type == "DISC":  # Disk
                if is_disc == "BDMV":
                    name = f"{title} ({year}) {alt_title} {season}{episode} {three_d} [{cut} {ratio} {edition} {repack} {resolution} {region} {uhd} {source} {hdr} {video_codec} {audio}{tag}] {lang_tag}"
                    potential_missing = ["edition", "region", "distributor"]
                if is_disc == "DVD":
                    name = f"{title} {alt_title} {season}{episode} {three_d} [{cut} {ratio} {edition} {repack} {source} {dvd_size} {audio}{tag}] {lang_tag}"
                    potential_missing = ["edition", "distributor"]
                elif is_disc == "HDDVD":
                    name = f"{title} {alt_title} ({year}) [{cut} {ratio} {edition} {repack} {resolution} {source} {video_codec} {audio}{tag}] {lang_tag}"
                    potential_missing = ["edition", "region", "distributor"]
            elif type == "REMUX" and source in ("BluRay", "HDDVD"):  # BluRay Remux
                name = f"{title} ({year}) {alt_title} {season}{episode} {episode_title} {part} [{three_d} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {audio}{tag}] {lang_tag}"  # SOURCE
                potential_missing = ["edition", "description"]
            elif type == "REMUX" and source in ("PAL DVD", "NTSC DVD"):  # DVD Remux
                name = f"{title} ({year}) {alt_title} {season}{episode} {episode_title} {part} [{cut} {ratio} {edition} {repack} {source} REMUX {audio}{tag}] {lang_tag}"  # SOURCE
                potential_missing = ["edition", "description"]
            elif type == "ENCODE":  # Encode
                name = f"{title} ({year}) {alt_title} {season}{episode} {episode_title} {part} [{cut} {ratio} {edition} {repack} {resolution} {uhd} {source} {audio} {hdr} {video_encode}{tag}] {lang_tag}"  # SOURCE
                potential_missing = ["edition", "description"]
            elif type == "WEBDL":  # WEB-DL
                name = f"{title} ({year}) {alt_title} {season}{episode} {episode_title} {part} [{cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEB-DL {audio} {hdr} {video_encode}{tag}] {lang_tag}"
                potential_missing = ["edition", "service"]
            elif type == "WEBRIP":  # WEBRip
                name = f"{title} ({year}) {alt_title} {season}{episode} {episode_title} {part} [{cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEBRip {audio} {hdr} {video_encode}{tag}] {lang_tag}"
                potential_missing = ["edition", "service"]
            elif type == "HDTV":  # HDTV
                name = f"{title} ({year}) {alt_title} {season}{episode} {episode_title} {part} [{cut} {ratio} {edition} {repack} {resolution} {source} {audio} {video_encode}{tag}] {lang_tag}"
                potential_missing = []

        parts = name.split()
        if "[" in parts:
            bracket_index = parts.index("[")
            sq_bracket = next((i for i, part in enumerate(parts[bracket_index + 1 :]) if part.strip()), None)
            if sq_bracket is not None:
                name = " ".join(parts[: bracket_index + 1]) + " ".join(parts[bracket_index + 1 :])

        return " ".join(name.split())
