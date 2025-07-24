from typing import Dict, List, Tuple

from src.utils.console import log


class NameGenerator:
    # fmt:off
    NAMING_TEMPLATES = {
        "MOVIE": {
            "DISC": {
                "BDMV": "{title} {aka} {year} {3D} {cut} {ratio} {edition} {repack} {resolution} {region} {uhd} {source} {hdr} {video_codec} {lang} {audio}",
                "DVD": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {source} {dvd_size} {lang} {audio}",
                "HDDVD": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {source} {video_codec} {lang} {audio}",
            },
            "REMUX": {
                "BluRay": "{title} {aka} {year} {3D} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {lang} {audio}",
                "HDDVD": "{title} {aka} {year} {3D} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {lang} {audio}",
                "DVD": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {source} REMUX {lang} {audio}",
            },
            "ENCODE": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} {lang} {audio} {hdr} {video_encode}",
            "WEBDL": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEB-DL {lang} {audio} {hdr} {video_encode}",
            "WEBRIP": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEBRip {lang} {audio} {hdr} {video_encode}",
            "HDTV": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {source} {lang} {audio} {video_encode}",
        },
        "TV": {
            "DISC": {
                "BDMV": "{title} {year} {aka} {season}{episode} {3D} {cut} {ratio} {edition} {repack} {resolution} {region} {uhd} {source} {hdr} {video_codec} {lang} {audio}",
                "DVD": "{title} {aka} {season}{episode} {3D} {cut} {ratio} {edition} {repack} {source} {dvd_size} {lang} {audio}",
                "HDDVD": "{title} {aka} {year} {cut} {ratio} {edition} {repack} {resolution} {source} {video_codec} {lang} {audio}",
            },
            "REMUX": {
                "BluRay": "{title} {year} {aka} {season}{episode} {episode_title} {part} {3D} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {lang} {audio}",
                "DVD": "{title} {year} {aka} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {source} REMUX {lang} {audio}",
            },
            "ENCODE": "{title} {year} {aka} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} {lang} {audio} {hdr} {video_encode}",
            "WEBDL": "{title} {year} {aka} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEB-DL {lang} {audio} {hdr} {video_encode}",
            "WEBRIP": "{title} {year} {aka} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEBRip {lang} {audio} {hdr} {video_encode}",
            "HDTV": "{title} {year} {aka} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {source} {lang} {audio} {video_encode}",
        },
        "MUSIC": {
            "ALAC": "{artist} - {album} ({year}) {edition} [{source}{type} {bit_depth}bit]",
            "FLAC": "{artist} - {album} ({year}) {edition} [{source}{type} {bit_depth}bit]",
            "WAV": "{artist} - {album} ({year}) {edition} [{source}{type} {bit_depth}bit]",
            "default": "{artist} - {album} ({year}) {edition} [{source}{type} {bitrate}]",
        },
    }

    POTENTIAL_MISSING = {
        "MOVIE": {
            "DISC": ["edition", "region", "distributor"],
            "REMUX": ["edition", "description"],
            "WEBDL": ["edition", "service"],
            "WEBRIP": ["edition", "service"],
            "default": [],
        },
        "TV": {
            "DISC": ["edition", "region", "distributor"],
            "REMUX": ["edition", "description"],
            "WEBDL": ["edition", "service"],
            "WEBRIP": ["edition", "service"],
            "default": [],
        },
        "MUSIC": {"default": []},
    }
    # fmt: on
    @staticmethod
    def preprocess_meta(meta: Dict) -> Dict:
        """Preprocess metadata to handle edge cases."""
        processed = meta.copy()
        if processed.get("use_original_tite", False):
            processed["title"] = processed["original_title"]
            processed["aka"] = ""
        if processed.get("no_season", False):
            processed["season"] = ""
        if processed.get("no_year", False):
            processed["year"] = ""
        if processed.get("no_aka", False):
            processed["aka"] = ""
        if processed.get("resolution") == "OTHER":
            processed["resolution"] = ""
        if processed["category"] == "TV":
            year = processed.get("year", "")
            if year.isdigit():
                processed["year"] = year
            elif processed.get("search_year", "").isdigit():
                processed["year"] = processed["search_year"]
            elif processed.get("imdb_info", {}).get("year", "").isdigit():
                processed["year"] = processed["imdb_info"]["year"]
        if processed.get("lang", "ENGLISH") == "ENGLISH":
            processed["lang"] = ""
        return processed

    @staticmethod
    def get_template(category: str, media_type: str, source: str = None, is_disc: str = None) -> str:
        """Retrieve the appropriate naming template."""
        templates = NameGenerator.NAMING_TEMPLATES.get(category, {})
        if category == "MUSIC":
            return templates.get(media_type, templates["default"])
        if media_type == "DISC":
            return templates[media_type].get(is_disc, "")
        if media_type == "REMUX":
            if source:
                if "DVD" in source.upper():
                    key = "DVD"
                elif "BLURAY" in source.upper():
                    key = "BluRay"
                elif "HDDVD" in source.upper():
                    key = "HDDVD"
                else:
                    key = source.split()[0]
            else:
                key = "BluRay"
            return templates[media_type].get(key, "")
        return templates.get(media_type, "")

    @staticmethod
    def get_potential_missing(category: str, media_type: str) -> List[str]:
        """Retrieve potential missing fields."""
        missing = NameGenerator.POTENTIAL_MISSING.get(category, {})
        return missing.get(media_type, missing["default"])

    @staticmethod
    def clean_filename(name: str) -> str:
        invalid = r'<>:"/\|?*'
        for char in invalid:
            name = name.replace(char, "-")
        return name.strip()

    async def get_name(self, meta: Dict) -> Tuple[str, str, str, List[str]]:
        """Generate the name based on metadata"""
        meta = self.preprocess_meta(meta)
        category, media_type = meta["category"], meta.get("type", "")
        source, is_disc = meta.get("source", ""), meta.get("is_disc", "")

        log.debug(f"[cyan]CATEGORY: {category}, TYPE: {media_type}")
        if meta.get("is_music", False):
            log.debug(f"ARTIST: {meta['artist']}, ALBUM: {meta['album']}, YEAR: {meta['year']}, SOURCE: {source}")

        # Get template and potential missing fields
        template = self.get_template(category, media_type, source, is_disc)
        potential_missing = self.get_potential_missing(category, media_type)

        # Handle MUSIC-specific formatting
        if category == "MUSIC":
            edition = meta.get("manual_edition", meta.get("edition", ""))
            meta["edition"] = f"[{edition}]" if edition else ""
            meta["bit_depth"] = f"{meta.get('bit_depth', '')}"
            meta["source"] = f"{source} " if source else ""

        placeholder_keys = [part.split("}")[0] for part in template.split("{")[1:]]
        safe_meta = {key: str(meta.get(key, "")) if meta.get(key) is not None else "" for key in placeholder_keys}
        name_notag = template
        for key in safe_meta:
            placeholder = f"{{{key}}}"
            value = safe_meta[key]
            name_notag = name_notag.replace(placeholder, value)

        # Clean up multiple spaces and strip
        name_notag = " ".join(name_notag.split())

        # Add tag if applicable
        tag = "" if meta.get("no_tag") or not meta.get("tag") else meta["tag"]
        name = name_notag + tag

        # Use manual name if provided and not None, otherwise use generated name
        name = meta.get("manual_name") if meta.get("manual_name") is not None else name
        clean_name = self.clean_filename(name)

        # Log if no meaningful name was generated
        if not name_notag or name_notag.isspace():
            log.warning(f"[yellow]Generated name is empty for category: {category}, type: {media_type}, source: {source}")
            return "Unnamed", name, clean_name, potential_missing

        return name_notag, name, clean_name, potential_missing
