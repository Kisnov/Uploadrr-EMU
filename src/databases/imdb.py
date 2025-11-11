import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiofiles
import langcodes
from imdbinfo import get_akas, get_movie, search_title

from src.utils.console import log

COUNTRY2LANG = {}


async def load_country_to_lang():
    global COUNTRY2LANG
    c2l_path = Path(__file__).parent.parent.parent / "data" / "country2lang.json"
    try:
        async with aiofiles.open(c2l_path, "r", encoding="utf-8") as f:
            content = await f.read()
            COUNTRY2LANG = json.loads(content)
    except Exception as e:
        log.error(f"Failed to load country2lang.json: {str(e)}")
        COUNTRY2LANG = {}


class IMDbHandler:
    """Handler for IMDb API operations using imdbinfo library."""

    async def get_imdb_info(self, imdb_id: str, meta: Dict) -> Dict:
        if int(imdb_id) == 0:
            return self._fallback_from_meta(meta)

        try:
            info = get_movie(imdb_id)
            if not info or not info.title:
                raise ValueError("Movie not found")

            aka, lang, akas_list = await self._get_akas(info, imdb_id)

            default_title = meta.get("title", "")
            return {
                "title": info.title or default_title,
                "year": info.year or meta["year"],
                "aka": aka,
                "akas": akas_list,
                "type": info.kind,
                "imdbID": info.imdbId or f"tt{imdb_id.zfill(7)}",
                "runtime": str(info.duration) if info.duration else "",
                "cover": info.cover_url or meta.get("poster", ""),
                "plot": info.plot or meta.get("overview", ""),
                "genres": ", ".join(info.genres) if info.genres else "",
                "keywords": ", ".join(info.storyline_keywords) if info.storyline_keywords else "",
                "soundmix": info.sound_mixes[0] if info.sound_mixes else None,
                "countries_of_origin": info.countries or [],
                "original_language": self._normalize_language(info.languages),
                "directors": [f"nm{d.imdb_id}" for d in info.directors] if info.directors else "",
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
            "akas": [],
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

    async def find_aka(self, imdb_id: str) -> Tuple[str, Optional[str]]:
        """Fetch IMDb AKA and original language."""
        if imdb_id == "0":
            return "", None
        try:
            movie = get_movie(imdb_id)
            if not movie:
                return "", None

            aka, lang, _ = await self._get_akas(movie, imdb_id)
            return (f" AKA {aka}" if aka else "", lang)
        except Exception as e:
            log.error(f"Failed to get IMDb AKA for ID {imdb_id}: {str(e)}")
            return "", None

    async def _get_akas(self, info, imdb_id: str) -> Tuple[str, Optional[str], List[Dict[str, str]]]:
        """Find AKA if not english + return all AKAs with language."""
        orig_lang = (info.languages[0] if info.languages else "").lower()
        akas_list = []

        try:
            akas_data = get_akas(f"tt{imdb_id}")
            akas = akas_data.akas if akas_data and hasattr(akas_data, "akas") else []

        except Exception as e:
            log.error(f"Failed to fetch AKAs for ID {imdb_id}: {str(e)}")
            akas = []

        # Build akas_list with 2-letter ISO language codes
        if COUNTRY2LANG == {}:
            await load_country_to_lang()

        for aka in akas:
            aka_info = {"title": aka.title}

            language_code = None
            country_code = getattr(aka, "country_code", None)

            # Try language_code
            if getattr(aka, "language_code", None) and aka.language_code != "N/A":
                language_code = aka.language_code

            # Fallback: map country → language
            elif country_code and country_code != "N/A":
                cc = country_code.upper()
                if cc in COUNTRY2LANG:
                    language_code = COUNTRY2LANG[cc]

            if language_code and language_code != "und":
                aka_info["language"] = language_code
            else:
                aka_info["country"] = getattr(aka, "country_name", country_code or "")

            akas_list.append(aka_info)

        # Dedupe AKAs
        seen = set()
        deduped_akas = []
        for item in akas_list:
            title_norm = item["title"].strip().lower()
            lang = item.get("language")
            country = item.get("country", "") if "country" in item else ""
            key = (title_norm, lang or country)
            if key not in seen:
                seen.add(key)
                deduped_akas.append(item)
        akas_list = deduped_akas

        if orig_lang == "en":
            return "", self._normalize_language(info.languages), akas_list

        if info.title_localized and info.title_localized != info.title:
            try:
                localized_lang_code = langcodes.find(info.title_localized).language
                orig_lang_code = langcodes.find(orig_lang).language
                if localized_lang_code == orig_lang_code:
                    return info.title_localized, self._normalize_language(info.languages), akas_list
            except LookupError:
                pass

        # Find an AKA matching the original language
        if akas and orig_lang:
            try:
                orig_lang_code = langcodes.find(orig_lang).language
                for aka in akas:
                    aka_lang_code = ""
                    if hasattr(aka, "language_code") and aka.language_code and aka.language_code != "N/A":
                        aka_lang_code = aka.language_code
                    elif hasattr(aka, "country_code") and aka.country_code and aka.country_code != "N/A":
                        country_code = aka.country_code.upper()
                        if country_code in COUNTRY2LANG:
                            aka_lang_code = COUNTRY2LANG[country_code]

                    # Check similarity to avoid redundant titles
                    if aka_lang_code and aka_lang_code == orig_lang_code:
                        similarity = SequenceMatcher(None, aka.title.lower(), info.title.lower()).ratio()
                        if similarity < 0.9:
                            return aka.title, self._normalize_language(info.languages), akas_list
            except LookupError:
                pass

        # Fallback to first AKA if no match found, but only if not too similar to main title
        if akas:
            similarity = SequenceMatcher(None, akas[0].title.lower(), info.title.lower()).ratio()
            if similarity < 0.9:
                return akas[0].title, self._normalize_language(info.languages), akas_list

        return "", self._normalize_language(info.languages), akas_list

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
            difference = SequenceMatcher(None, meta["title"].lower(), meta["aka"].lower()).ratio()
            if difference >= 0.9 or meta["aka"].strip() == "" or meta["aka"].lower() in meta["title"].lower():
                meta["aka"] = ""
            if f"({meta['year']})" in meta["aka"]:
                meta["aka"] = meta["aka"].replace(f"({meta['year']})", "").strip()
        return meta
