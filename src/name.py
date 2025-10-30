from typing import Any, Dict, List, Optional, Tuple

from src.utils.console import log


class TemplateContext:
    """Base template context with core functionality."""

    def __init__(self, meta: Dict[str, Any]):
        self._meta = meta
        self._overrides = {}

    def set_overrides(self, overrides: Dict[str, Any]):
        """Set tracker-specific overrides."""
        self._overrides = overrides

    def get(self, key: str, override: str = "") -> str:
        """Get value with tracker-specific overrides taking precedence."""
        if key in self._overrides and self._overrides[key] is not None:
            return str(self._overrides[key])

        # Fall back to meta
        value = self._meta.get(key, override)
        return str(value) if value is not None else ""

    def __getattr__(self, key: str) -> str:
        """Allow dot notation access with overrides."""
        return self.get(key)

    def to_dict(self) -> Dict[str, str]:
        """Convert meta + overrides into a flat dict for template formatting."""
        result = {}
        keys = set(self._meta.keys()) | set(self._overrides.keys())
        for key in keys:
            result[key] = self.get(key)
        return result


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
            "ALAC": "{artist} - {album} ({year}) {edition} [{source} {type} {bit_depth}bit]",
            "FLAC": "{artist} - {album} ({year}) {edition} [{source} {type} {bit_depth}bit]",
            "WAV": "{artist} - {album} ({year}) {edition} [{source} {type} {bit_depth}bit]",
            "default": "{artist} - {album} ({year}) {edition} [{source} {type} {bitrate}]",
        },
    }
    # fmt: on

    @staticmethod
    def get_template(
        category: str,
        media_type: str,
        source: str = None,
        is_disc: str = None,
        custom_templates: Optional[Dict] = None,
        tracker: str = None,
    ) -> str:
        """Retrieve the appropriate naming template."""
        # Try custom templates first - handle flat structure
        if custom_templates and category in custom_templates:
            cat_templates = custom_templates[category]

            # If it's a flat template (set or string), return it directly
            if isinstance(cat_templates, (set, str)):
                if isinstance(cat_templates, set):
                    # Extract the template string from the set
                    return next(iter(cat_templates)) if cat_templates else ""
                return cat_templates

            # If it's a dict (hierarchical structure), use the original logic
            if media_type in cat_templates:
                template_or_dict = cat_templates[media_type]
                if isinstance(template_or_dict, dict):
                    if media_type == "DISC" and is_disc:
                        return template_or_dict.get(is_disc, "")
                    elif media_type == "REMUX" and source:
                        source_key = "BluRay"
                        if "DVD" in source.upper():
                            source_key = "DVD"
                        elif "HDDVD" in source.upper():
                            source_key = "HDDVD"
                        return template_or_dict.get(source_key, "")
                    return list(template_or_dict.values())[0] if template_or_dict else ""
                return template_or_dict

        # Fall back to built-in templates (original logic)
        templates = NameGenerator.NAMING_TEMPLATES.get(category, {})
        if category == "MUSIC":
            return templates.get(media_type, templates["default"])
        if media_type == "DISC":
            return templates[media_type].get(is_disc, "")
        if media_type == "REMUX":
            source_key = "BluRay"
            if source:
                if "DVD" in source.upper():
                    source_key = "DVD"
                elif "HDDVD" in source.upper():
                    source_key = "HDDVD"
            return templates[media_type].get(source_key, "")
        return templates.get(media_type, "")

    @staticmethod
    def clean_filename(name: str) -> str:
        """Clean filename of invalid characters."""
        invalid = r'<>:"/\|?*'
        for char in invalid:
            name = name.replace(char, "-")
        return " ".join(name.split())

    @staticmethod
    def _preprocess_meta_for_context(meta: Dict) -> Dict:
        """Preprocess meta data for context usage."""
        processed = meta.copy()

        # Handle title variations
        if processed.get("use_original_title", False):
            processed["title"] = processed.get("original_title", "")

        # Handle resolution
        if processed.get("resolution") == "OTHER":
            processed["resolution"] = ""

        # Handle language defaults
        if processed.get("lang", "ENGLISH") == "ENGLISH":
            processed["lang"] = ""

        return processed

    async def get_name(self, meta: Dict) -> Tuple[str, str, str, List[str]]:
        """Generate the main name using context."""
        processed_meta = self._preprocess_meta_for_context(meta)
        context = TemplateContext(processed_meta)

        category, media_type = meta.get("category", ""), meta.get("type", "")
        source, is_disc = meta.get("source", ""), meta.get("is_disc", "")

        template = self.get_template(category, media_type, source, is_disc)

        if not template:
            name_notag = "NO_TEMPLATE_FOUND"
        else:
            try:
                formatted_dict = context.to_dict()
                name_notag = template.format(**formatted_dict)
                name_notag = " ".join(name_notag.split())
            except (KeyError, ValueError) as e:
                log.warning(f"Template formatting error: {e}")
                name_notag = "TEMPLATE_ERROR"

        tag = "" if meta.get("no_tag") or not meta.get("tag") else meta["tag"]
        name = name_notag + tag if tag and not name_notag.endswith(tag) else name_notag
        clean_name = self.clean_filename(name)

        # Store in meta
        meta.update({"name_notag": name_notag, "name": name, "clean_name": clean_name})

        return name_notag, name, clean_name, []

    async def generate_tracker_name(
        self, meta: Dict, tracker: str, custom_templates: Optional[Dict] = None, overrides: Optional[Dict] = None
    ) -> str:
        """Generate tracker-specific name with tracker-specific overrides."""
        processed_meta = self._preprocess_meta_for_context(meta)
        context = TemplateContext(processed_meta)

        # Set tracker-specific overrides
        if overrides:
            context.set_overrides(overrides)

        category, media_type = meta.get("category", ""), meta.get("type", "")
        source, is_disc = meta.get("source", ""), meta.get("is_disc", "")

        template = self.get_template(category, media_type, source, is_disc, custom_templates, tracker)

        if not template:
            tracker_name_notag = "NO_TEMPLATE_FOUND"
        else:
            try:
                formatted_dict = context.to_dict()
                tracker_name_notag = template.format(**formatted_dict)
                tracker_name_notag = " ".join(tracker_name_notag.split())
            except (KeyError, ValueError) as e:
                log.warning(f"Tracker {tracker} template formatting error: {e}")
                tracker_name_notag = "TEMPLATE_ERROR"

        # Handle tag inclusion
        tag = "" if meta.get("no_tag") or not meta.get("tag") else meta["tag"]
        if "{tag}" in template:
            tracker_name = tracker_name_notag  # Tag already included
        else:
            tracker_name = tracker_name_notag + tag

        return tracker_name
