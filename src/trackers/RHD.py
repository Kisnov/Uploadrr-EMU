from __future__ import annotations

from typing import Any, Dict

from src.mediainfo import MediaInfoHandler

from .UNIT3D import Unit3d


class RHD(Unit3d):
    tracker = "RHD"
    source_flag = "RocketHD"
    domain = "https://rocket-hd.cc"
    allowed_imghosts = []
    # Updated 25-11-11 == https://rocket-hd.cc/wikis/4
    banned_groups = [
        "1XBET",
        "266ers",
        "2BA",
        "BB",
        "CAM",
        "FSX",
        "FuN",
        "GTF",
        "HELD",
        "JellyfinPlex",
        "LD",
        "MagicX",
        "MEGA",
        "Mic",
        "MTZ",
        "PaTroL",
        "Taylor.D",
        "TS",
        "Whistler",
        "w00t",
        "WOTT",
    ]

    @staticmethod
    def _detect_german(uuid: str) -> str:
        """
        Return one of:  "", "GERMAN", "GERMAN DL", "GERMAN SUBBED"

        The MediaInfo JSON lives in ``MediaInfoHandler._instance._cache[uuid]``.
        """
        handler = MediaInfoHandler._instance
        if handler is None or uuid not in handler._cache:
            return ""

        mi = handler._cache[uuid]
        tracks = mi.get("media", {}).get("track", [])
        if not isinstance(tracks, list):
            tracks = []

        german_audio = False
        other_audio_langs = set()
        german_text = False

        for t in tracks:
            typ = t.get("@type", "").lower()
            lang = str(t.get("Language", "")).lower()
            title = str(t.get("Title", "")).lower()

            # Filter out musical scores/commentary
            is_commentary = "comment" in title or "review" in title
            is_nonverbal = lang == "zxx" or "score" in title

            if typ == "audio" and not is_commentary and not is_nonverbal:
                if lang in {"de", "ger", "deu"} or "german" in title:
                    german_audio = True
                else:
                    other_audio_langs.add(lang)

            if typ == "text":
                if lang in {"de", "ger", "deu"} or "german" in title:
                    german_text = True

        if german_text and not german_audio:
            return "GERMAN SUBBED"
        if german_audio:
            if len(other_audio_langs) >= 2:
                return "GERMAN ML"
            elif len(other_audio_langs) == 1:
                return "GERMAN DL"
            else:
                return "GERMAN"
        return ""

    @staticmethod
    def _build_overrides(meta: Dict[str, Any]) -> Dict[str, Any]:
        """Values that are injected into the template before formatting."""
        overrides: Dict[str, Any] = {}

        # ---- GERMAN (DL/SUBBED)-----------------------------------------
        overrides["lang"] = RHD._detect_german(meta["uuid"])

        # ---- TITLE -----------------------------------------------------
        if meta.get("original_language") == "de":
            overrides["title"] = meta.get("original_title") or meta.get("Title", "")

        else:
            akas = meta.get("imdb_info", {}).get("akas", [])
            ger_title = None

            for aka in akas:
                if aka.get("language") == "de":
                    ger_title = aka["title"]
                    break

            if ger_title:
                main_title = meta.get("Title", "")
                if ger_title.strip().lower() != main_title.strip().lower():
                    overrides["title"] = f"{ger_title} AKA {main_title}"
                else:
                    overrides["title"] = ger_title
            else:
                aka = meta.get("aka", "")
                if aka:
                    aka = f" AKA {aka}"
                overrides["title"] = f"{meta.get('Title', '')}{aka}"

        # ---- GROUP TAG -------------------------------------------------
        tag = meta.get("tag", "")
        if not tag:
            tag = "NOGRP"
        overrides["tag"] = tag

        return overrides

    async def edit_name(self, meta: Dict[str, Any]) -> str:
        from src.name import NameGenerator

        template = {
            "MOVIE": "{title} {year} {lang} {audio} {resolution} {source} {hdr} {video_codec}{tag}",
            "TV": "{title} {year_opt} {season}{episode} {episode_title} {lang} {audio} {resolution} {source} {hdr} {video_codec}{tag}",
        }

        overrides = self._build_overrides(meta)
        rhd_name = await NameGenerator().generate_tracker_name(meta, self.tracker, template, overrides)

        return rhd_name
