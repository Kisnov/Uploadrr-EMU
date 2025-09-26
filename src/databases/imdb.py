from difflib import SequenceMatcher
from typing import Dict, Optional, Tuple

from imdbinfo import get_akas, get_movie, search_title

from src.utils.console import log


class IMDbHandler:
    """Handler for IMDb API operations using imdbinfo library."""

    async def get_imdb_info(self, imdb_id: str, meta: Dict) -> Dict:
        imdb_id = str(imdb_id)
        if int(imdb_id) == 0:
            return self._fallback_from_meta(meta)

        try:
            info = get_movie(imdb_id)
            if not info or not info.title:
                raise ValueError("Movie not found")

            aka = ""
            try:
                akas_data = get_akas(f"tt{imdb_id}")
                if akas_data and akas_data.akas:
                    original = next((a.title for a in akas_data.akas if a.attributes and "original" in a.attributes), None)
                    aka = original or akas_data.akas[0].title
            except Exception:
                aka = info.title_localized or ""

            aka = aka.replace(" - IMDb", "").replace("\u00ae", "") if aka else ""

            default_title = meta.get("title", "")
            return {
                "title": info.title or default_title,
                "year": info.year or meta["year"],
                "aka": aka,
                "type": info.kind,
                "imdbID": info.imdbId or f"tt{imdb_id.zfill(7)}",
                "runtime": str(info.duration) if info.duration else meta.get("runtime", "60"),
                "cover": info.cover_url or meta.get("poster", ""),
                "plot": info.plot or meta.get("overview", ""),
                "genres": ", ".join(info.genres) if info.genres else meta.get("genres", ""),
                "soundmix": info.sound_mixes[0] if info.sound_mixes else None,
                "countries_of_origin": info.countries or [],
                "original_language": self._normalize_language(info.languages),
                "directors": [f"nm{d.imdb_id}" for d in info.directors] if info.directors else meta.get("tmdb_directors", []),
            }
        except Exception as e:
            log.error(f"Failed to fetch IMDb info for ID {imdb_id}: {str(e)}")
            return self._fallback_from_meta(meta)

    def _normalize_language(self, languages: list) -> Optional[str]:
        return languages[0] if len(languages) == 1 else None

    def _fallback_from_meta(self, meta: Dict) -> Dict:
        """Provide fallback data from meta if IMDb fetch fails."""
        return {
            "title": meta["title"],
            "year": meta["year"],
            "aka": "",
            "type": None,
            "runtime": meta.get("runtime", "60"),
            "cover": meta.get("poster", ""),
            "plot": meta.get("overview", ""),
            "genres": meta.get("genres", ""),
            "soundmix": None,
            "countries_of_origin": [],
            "original_language": meta.get("original_language"),
            "directors": meta.get("tmdb_directors", []),
        }

    async def search_imdb(self, filename: str, search_year: int) -> str:
        """Search IMDb for a movie by filename and year."""
        try:
            results = search_title(filename)
            for movie in results.titles:
                if movie.title and filename.lower() in movie.title.lower() and movie.year == search_year:
                    return movie.id
            return "0"
        except Exception as e:
            log.error(f"IMDb search failed for {filename} ({search_year}): {str(e)}")
            return "0"

    async def get_imdb_aka(self, imdb_id: str) -> Tuple[str, Optional[str]]:
        """Fetch IMDb AKA and original language."""
        if imdb_id == "0":
            return "", None
        try:
            imdb_id = imdb_id.replace("tt", "")
            movie = get_movie(imdb_id)
            lang = self._normalize_language(movie.languages) if movie else None

            aka = ""
            akas_data = get_akas(f"tt{imdb_id}")
            if akas_data and akas_data.akas:
                original = next((a.title for a in akas_data.akas if a.attributes and "original" in a.attributes), None)
                aka = original or akas_data.akas[0].title

            aka = aka.replace(" - IMDb", "").replace("\u00ae", "") if aka else ""
            return f" AKA {aka}" if aka else "", lang
        except Exception as e:
            log.error(f"Failed to get IMDb AKA for ID {imdb_id}: {str(e)}")
            return "", None

    async def imdb_other_meta(self, meta: Dict) -> Dict:
        """Update meta with additional IMDb metadata."""
        meta["imdb_info"] = await self.get_imdb_info(meta["imdb_id"], meta)
        meta.update({
            "title": meta["imdb_info"]["title"],
            "aka": meta["imdb_info"]["aka"],
            "poster": meta["imdb_info"]["cover"],
            "original_language": meta["imdb_info"]["original_language"],
            "overview": meta["imdb_info"]["plot"],
        })

        if meta["aka"]:
            difference = SequenceMatcher(None, meta["title"].lower(), meta["aka"][5:].lower()).ratio()
            if difference >= 0.9 or meta["aka"][5:].strip() == "" or meta["aka"][5:].strip().lower() in meta["title"].lower():
                meta["aka"] = ""
            if f"({meta['year']})" in meta["aka"]:
                meta["aka"] = meta["aka"].replace(f"({meta['year']})", "").strip()
        return meta
