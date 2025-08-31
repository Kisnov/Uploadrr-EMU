from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d


class LT(Unit3d):
    tracker = "LT"
    source_flag = 'Lat-Team "Poder Latino"'
    domain = "https://lat-team.com"
    allowed_imghosts = []
    banned_groups = [""]

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        category_name = meta["category"]
        anime = meta.get("anime", False)
        overview = meta.get("overview", "")
        keywords = meta.get("keywords", "")
        keyword_list = [k.strip() for k in keywords.lower().split(",")]
        category_id = {"MOVIE": "1", "TV": "2"}.get(category_name, "0")
        if category_name == "TV":
            if anime:
                category_id = "5"
            elif "telenovela" in overview.lower() or "telenovela" in keyword_list:
                category_id = "8"
            else:
                category_id = "2"
        return category_id

    def get_subs_or_cast(self, meta: Dict[str, Any]) -> str:
        media_info = meta.get("mediainfo")
        is_disc = meta.get("is_disc", False)
        imdb_info = meta.get("imdb_info", {})

        if is_disc:
            if is_disc == "BDMV":
                disc = meta["discs"][0]
                subtitles = disc["bdinfo"].get("subtitles", [])
                if "spanish" in [s.lower() for s in subtitles]:
                    return " [SUBS]"
            return ""
        else:
            if not media_info or "media" not in media_info:
                return ""
            audio_tracks = [track for track in media_info["media"]["track"] if track.get("StreamKind_String", "").lower() == "audio"]
            text_tracks = [track for track in media_info["media"]["track"] if track.get("StreamKind_String", "").lower() == "text"]
            for track in audio_tracks:
                lang = track.get("Language", "")
                lang_string2 = track.get("Language_String2", "").lower()
                title = track.get("Title", "").lower()
                if not any(title.find(word) != -1 for word in ["comment", "director", "review"]):
                    if lang.lower() in ("es", "spa", "spanish") or lang_string2 == "spanish" or "spanish" in title:
                        return " [LATINO]"
            for track in text_tracks:
                lang = track.get("Language", "")
                lang_string2 = track.get("Language_String2", "").lower()
                title = track.get("Title", "").lower()
                if lang.lower() in ("es", "spa", "spanish") or lang_string2 == "spanish" or "spanish" in title:
                    return " [SUBS]"
            if imdb_info.get("language", "").lower() in ("spanish", "es", "spa"):
                return " [LATINO]"
            return ""
