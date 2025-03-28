from typing import Dict, Tuple, List
import json

from src.console import console, log

class NameGenerator:
    NAMING_TEMPLATES = {
        "MOVIE": {
            "DISC": {
                "BDMV": "{title} {alt_title} {year} {three_d} {cut} {ratio} {edition} {repack} {resolution} {region} {uhd} {source} {hdr} {video_codec} {audio}",
                "DVD": "{title} {alt_title} {year} {cut} {ratio} {edition} {repack} {source} {dvd_size} {audio}",
                "HDDVD": "{title} {alt_title} {year} {cut} {ratio} {edition} {repack} {resolution} {source} {video_codec} {audio}"
            },
            "REMUX": {
                "BluRay": "{title} {alt_title} {year} {three_d} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {audio}",
                "HDDVD": "{title} {alt_title} {year} {three_d} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {audio}",
                "DVD": "{title} {alt_title} {year} {cut} {ratio} {edition} {repack} {source} REMUX {audio}"
            },
            "ENCODE": "{title} {alt_title} {year} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} {audio} {hdr} {video_encode}",
            "WEBDL": "{title} {alt_title} {year} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEB-DL {audio} {hdr} {video_encode}",
            "WEBRIP": "{title} {alt_title} {year} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEBRip {audio} {hdr} {video_encode}",
            "HDTV": "{title} {alt_title} {year} {cut} {ratio} {edition} {repack} {resolution} {source} {audio} {video_encode}"
        },
        "TV": {
            "DISC": {
                "BDMV": "{title} {year} {alt_title} {season}{episode} {three_d} {cut} {ratio} {edition} {repack} {resolution} {region} {uhd} {source} {hdr} {video_codec} {audio}",
                "DVD": "{title} {alt_title} {season}{episode} {three_d} {cut} {ratio} {edition} {repack} {source} {dvd_size} {audio}",
                "HDDVD": "{title} {alt_title} {year} {cut} {ratio} {edition} {repack} {resolution} {source} {video_codec} {audio}"
            },
            "REMUX": {
                "BluRay": "{title} {year} {alt_title} {season}{episode} {episode_title} {part} {three_d} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} REMUX {hdr} {video_codec} {audio}",
                "DVD": "{title} {year} {alt_title} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {source} REMUX {audio}"
            },
            "ENCODE": "{title} {year} {alt_title} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {uhd} {source} {audio} {hdr} {video_encode}",
            "WEBDL": "{title} {year} {alt_title} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEB-DL {audio} {hdr} {video_encode}",
            "WEBRIP": "{title} {year} {alt_title} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {uhd} {service} WEBRip {audio} {hdr} {video_encode}",
            "HDTV": "{title} {year} {alt_title} {season}{episode} {episode_title} {part} {cut} {ratio} {edition} {repack} {resolution} {source} {audio} {video_encode}"
        },
        "MUSIC": {
            "ALAC": "{artist} - {album} ({year}) {edition} [{source}{type} {bit_depth}bit]",
            "FLAC": "{artist} - {album} ({year}) {edition} [{source}{type} {bit_depth}bit]",
            "WAV": "{artist} - {album} ({year}) {edition} [{source}{type} {bit_depth}bit]",
            "default": "{artist} - {album} ({year}) {edition} [{source}{type} {bitrate}]"
        }
    }

    POTENTIAL_MISSING = {
        "MOVIE": {
            "DISC": ["edition", "region", "distributor"],
            "REMUX": ["edition", "description"],
            "WEBDL": ["edition", "service"],
            "WEBRIP": ["edition", "service"],
            "default": []
        },
        "TV": {
            "DISC": ["edition", "region", "distributor"],
            "REMUX": ["edition", "description"],
            "WEBDL": ["edition", "service"],
            "WEBRIP": ["edition", "service"],
            "default": []
        },
        "MUSIC": {"default": []}
    }

    @staticmethod
    def preprocess_meta(meta: Dict) -> Dict:
        """Preprocess metadata to handle edge cases."""
        processed = meta.copy()
        if processed.get("no_season", False):
            processed["season"] = ""
        if processed.get("no_year", False):
            processed["year"] = ""
        if processed.get("no_aka", False):
            processed["alt_title"] = ""
        if processed.get("resolution") == "OTHER":
            processed["resolution"] = ""
        if processed["category"] == "TV" and processed.get("search_year", "") != "":
            processed["year"] = processed["year"]
        return processed

    @staticmethod
    def get_template(category: str, type_: str, source: str = None, is_disc: str = None) -> str:
        """Retrieve the appropriate naming template."""
        templates = NameGenerator.NAMING_TEMPLATES.get(category, {})
        if category == "MUSIC":
            return templates.get(type_, templates["default"])
        if type_ == "DISC":
            return templates[type_].get(is_disc, "")
        if type_ == "REMUX":
            return templates[type_].get(source.split()[0] if source else "BluRay", "")
        return templates.get(type_, "")

    @staticmethod
    def get_potential_missing(category: str, type_: str) -> List[str]:
        """Retrieve potential missing fields."""
        missing = NameGenerator.POTENTIAL_MISSING.get(category, {})
        return missing.get(type_, missing["default"])

    @staticmethod
    def clean_filename(name: str) -> str:
        invalid = r'<>:"/\|?*'
        for char in invalid:
            name = name.replace(char, '-')
        return name.strip()

    async def get_name(self, meta: Dict) -> Tuple[str, str, str, List[str]]:
        """Generate the name based on metadata."""
        meta = self.preprocess_meta(meta)
        category, type_ = meta["category"], meta.get("type", "")
        source, is_disc = meta.get("source", ""), meta.get("is_disc", "")

        log.debug(f"[cyan]CATEGORY: {category}, TYPE: {type_}")
        if meta.get("is_music", False):
            log.debug(f"ARTIST: {meta['artist']}, ALBUM: {meta['album']}, YEAR: {meta['year']}, SOURCE: {source}")

        # Get template and potential missing fields
        template = self.get_template(category, type_, source, is_disc)
        potential_missing = self.get_potential_missing(category, type_)

        # Handle MUSIC-specific formatting
        if category == "MUSIC":
            edition = meta.get("manual_edition", meta.get("edition", ""))
            meta["edition"] = f"[{edition}]" if edition else ""
            meta["bit_depth"] = f"{meta.get('bit_depth', '')}bit" if meta.get("bit_depth") else ""
            meta["source"] = f"{source} " if source else ""

        try:
            # Generate base name
            name_notag = template.format(**meta)
            name_notag = " ".join(name_notag.split())

            # Add tag if applicable
            tag = "" if meta.get("no_tag") or not meta.get("tag") else meta["tag"]
            name = name_notag + tag

            # Use manual name if provided
            name = meta.get("manual_name", name)
            clean_name = self.clean_filename(name)

            return name_notag, name, clean_name, potential_missing

        except Exception as e:
            console.print(f"[bold red]Error generating name: {e}")
            console.print(f"--category [yellow]{category}")
            console.print(f"--type [yellow]{type_}")
            console.print(f"--source [yellow]{source}")
            return "Error in Name Generation", "", "", []

