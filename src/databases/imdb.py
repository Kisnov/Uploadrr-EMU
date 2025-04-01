from typing import Dict, Optional, Tuple

from imdb import Cinemagoer
from difflib import SequenceMatcher

from src.console import log


class IMDbHandler:
    """Handler for IMDb API operations."""
    
    def __init__(self):
        self.ia = Cinemagoer()

    async def get_imdb_info(self, imdb_id: str, meta: Dict) -> Dict:
        """Fetch IMDb information for a given ID."""
        imdb_id = str(imdb_id).replace('tt', '')
        if int(imdb_id) == 0:
            return self._fallback_from_meta(meta)

        try:
            info = self.ia.get_movie(imdb_id)
            log.ddebug(f"IMDb response for ID {imdb_id}: {info.data}")
            self.ia.update(info, ['technical'])
            default_title = meta.get('title', '')
            return {
                'title': info.get('title', default_title) or default_title,
                'year': info.get('year', meta['year']),
                'aka': info.get('original title', info.get('localized title', meta['title'])).replace(' - IMDb', '').replace('\u00ae', ''),
                'type': info.get('kind'),
                'imdbID': info.get('imdbID', f"tt{imdb_id.zfill(7)}"),
                'runtime': info.get('runtimes', ['0'])[0],
                'cover': info.get('full-size cover url', '').replace(".jpg", "._V1_FMjpg_UX750_.jpg") or meta.get('poster', ''),
                'plot': info.get('plot', [''])[0],
                'genres': ', '.join(info.get('genres', [])),
                'soundmix': info.get('sound mix'),
                'countries_of_origin': info.get('countries', []),
                'original_language': self._normalize_language(info.get('language codes', [])),
                'directors': [f"nm{d.getID()}" for d in info.get('directors', [])] or meta.get('tmdb_directors', [])
            }
        except Exception as e:
            log.error(f"Failed to fetch IMDb info for ID {imdb_id}: {str(e)}")
            return self._fallback_from_meta(meta)

    def _normalize_language(self, languages: list) -> Optional[str]:
        """Normalize language list to single value."""
        return languages[0] if len(languages) == 1 else None

    def _fallback_from_meta(self, meta: Dict) -> Dict:
        """Provide fallback data from meta if IMDb fetch fails."""
        return {
            'title': meta['title'],
            'year': meta['year'],
            'aka': '',
            'type': None,
            'runtime': meta.get('runtime', '60'),
            'cover': meta.get('poster', ''),
            'plot': meta.get('overview', ''),
            'genres': meta.get('genres', ''),
            'soundmix': None,
            'countries_of_origin': [],
            'original_language': meta.get('original_language'),
            'directors': meta.get('tmdb_directors', [])
        }

    async def search_imdb(self, filename: str, search_year: int) -> str:
        """Search IMDb for a movie by filename and year."""
        try:
            search = self.ia.search_movie(filename)
            for movie in search:
                if filename.lower() in movie.get('title', '').lower() and movie.get('year') == search_year:
                    return str(movie.movieID).replace('tt', '')
        except Exception as e:
            log.error(f"IMDb search failed for {filename} ({search_year}): {str(e)}")
        return '0'

    async def imdb_other_meta(self, meta: Dict) -> Dict:
        """Update meta with additional IMDb metadata."""
        meta['imdb_info'] = await self.get_imdb_info(meta['imdb_id'], meta)
        meta.update({
            'title': meta['imdb_info']['title'],
            'aka': meta['imdb_info']['aka'],
            'poster': meta['imdb_info']['cover'],
            'original_language': meta['imdb_info']['original_language'],
            'overview': meta['imdb_info']['plot']
        })
        
        if meta['aka']:
            difference = SequenceMatcher(None, meta['title'].lower(), meta['aka'][5:].lower()).ratio()
            if difference >= 0.9 or meta['aka'][5:].strip() == "" or meta['aka'][5:].strip().lower() in meta['title'].lower():
                meta['aka'] = ""
            if f"({meta['year']})" in meta['aka']:
                meta['aka'] = meta['aka'].replace(f"({meta['year']})", "").strip()
        return meta

    async def get_imdb_aka(self, imdb_id: str) -> Tuple[str, Optional[str]]:
        """Fetch IMDb AKA and original language."""
        if imdb_id == "0":
            return "", None
        try:
            result = self.ia.get_movie(imdb_id.replace('tt', ''))
            original_language = self._normalize_language(result.get('language codes', []))
            aka = result.get('original title', result.get('localized title', "")).replace(' - IMDb', '').replace('\u00ae', '')
            return f" AKA {aka}" if aka else "", original_language
        except Exception as e:
            log.error(f"Failed to get IMDb AKA for ID {imdb_id}: {str(e)}")
            return "", None