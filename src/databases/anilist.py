import asyncio
import re
from typing import Dict, Tuple, Optional

import httpx
from difflib import SequenceMatcher

from src.console import log


class AniListHandler:
    """Handler for AniList GraphQL API operations."""

    TITLE_CLEANER = re.compile(r"[^0-9a-zA-Z\[\]]+")
    JAPANESE_CLEANER = re.compile(
        r"[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]+ (?=[A-Za-z ]+–)", re.U)

    def __init__(self):
        self.client = httpx.AsyncClient(base_url="https://graphql.anilist.co", timeout=10.0)
        self.semaphore = asyncio.Semaphore(10)  # AniList rate limit: ~90 req/min

    async def rate_limit(self) -> None:
        async with self.semaphore:
            await asyncio.sleep(0.1)

    async def get_anime(self, response: Dict, meta: Dict) -> Tuple[int, str, bool]:
        """Determine if content is anime and fetch related data from AniList."""
        tmdb_name = meta['title']
        original_language = meta.get('original_language') or response.get('original_language', '')
        original_title = meta.get('original_title') or response.get('original_title', tmdb_name)
        alt_name = meta.get('aka', "")
        anime = False
        animation = any(genre['id'] == 16 for genre in response.get('genres', []))
        
        if original_language in ('ja', 'ko') and animation:
            fallback_titles = [tmdb_name, meta.get('aka', '').replace(" AKA ", "")] if meta.get('aka') else [tmdb_name]
            romaji, mal_id, _, _, _ = await self.get_romaji(
                original_title,
                meta.get('mal'),
                meta.get('search_year'),
                fallback_titles
            )
            alt_name = f" AKA {romaji}" if romaji else alt_name
            anime = True
        else:
            mal_id = meta.get('mal_id', 0) or meta.get('mal', 0) or 0
        
        return mal_id, alt_name, anime

    async def get_romaji(self, title: str, mal: Optional[int], year: Optional[str] = None, 
                        fallback_titles: Optional[list] = None) -> Tuple[str, int, str, str, int]:
        """Fetch romaji title and related anime data from AniList, stopping once a valid match is found.

        Args:
            title: Primary title to search (preferably native if Japanese/Korean).
            mal: MyAnimeList ID if available (optional).
            year: Release year for filtering (optional).
            fallback_titles: List of alternative titles to try if primary fails.

        Returns:
            Tuple of (romaji title, MAL ID, English title, season year, episodes).
        """
        titles_to_try = [title] + (fallback_titles or [])
        
        # If MAL ID is provided, use it and return immediately
        if mal:
            query, variables = self._build_query(None, mal)
            result = await self._execute_query(query, variables, year)
            if result[1]:  # If MAL ID is valid
                log.debug(f"Matched MAL ID {mal} directly")
                return result
            log.debug(f"MAL ID {mal} returned no results, falling back to title search")

        # Try each title, stopping on first valid match
        for search_title in titles_to_try:
            if self._is_native_script(search_title):
                cleaned_title = search_title.strip()
            else:
                cleaned_title = self.TITLE_CLEANER.sub("", search_title.lower().replace(" ", "").replace("-", "").replace("The Movie", "")).strip()
            
            query, variables = self._build_query(cleaned_title, None)
            result = await self._execute_query(query, variables, year)
            if result[1]:  # If MAL ID is found
                log.debug(f"Matched '{search_title}' to MAL ID {result[1]}")
                return result
            log.debug(f"No MAL ID found for '{search_title}', trying next title")
        
        log.debug(f"No valid AniList results for {title} or fallbacks")
        return "", 0, "", "", 0

    async def _execute_query(self, query: str, variables: Dict, year: Optional[str]) -> Tuple[str, int, str, str, int]:
        """Execute AniList GraphQL query and parse results."""
        await self.rate_limit()
        try:
            response = await self.client.post("", json={'query': query, 'variables': variables})
            response.raise_for_status()
            media = response.json()['data']['Page']['media']
            
            if media and year:
                media = [m for m in media if str(m.get('seasonYear', '')) == year or not year]
            if media:
                return self._parse_response(media, variables.get('search', ''))
            return "", 0, "", "", 0
        except httpx.HTTPStatusError as e:
            log.error(f"AniList API error: {e.response.status_code} - {e.response.text}")
            return "", 0, "", "", 0
        except httpx.RequestError as e:
            log.error(f"AniList request failed: {str(e)}")
            return "", 0, "", "", 0

    def _build_query(self, tmdb_name: str, mal: Optional[int]) -> Tuple[str, Dict]:
        """Build AniList GraphQL query based on input."""
        if mal:
            query = '''
                query ($search: Int) {
                    Page (page: 1) { media (idMal: $search, type: ANIME, sort: SEARCH_MATCH) {
                        id idMal title { romaji english native } seasonYear episodes
                    }}
                }
            '''
            return query, {'search': mal}
        query = '''
            query ($search: String) {
                Page (page: 1) { media (search: $search, type: ANIME, sort: SEARCH_MATCH) {
                    id idMal title { romaji english native } seasonYear episodes
                }}
            }
        '''
        return query, {'search': tmdb_name}

    def _parse_response(self, media: list, search_name: str) -> Tuple[str, int, str, str, int]:
        """Parse AniList response to extract the first good match and stop."""
        min_similarity = 0.8
        
        for anime in media:
            for title in anime['title'].values():
                if title:
                    similarity = SequenceMatcher(None, self.JAPANESE_CLEANER.sub("", title.lower()), search_name.lower()).ratio()
                    log.debug(f"Comparing '{title}' to '{search_name}': similarity={similarity:.2f}")
                    if similarity >= min_similarity:
                        log.info(f"Matched '{search_name}' to '{anime['title']['romaji']}' (MAL ID: {anime['idMal']})")
                        return (
                            anime['title'].get('romaji', anime['title'].get('english', "")),
                            anime.get('idMal', 0),
                            anime['title'].get('english', anime['title'].get('romaji', "")),
                            str(anime.get('seasonYear', "")),
                            anime.get('episodes', 0) or 0
                        )
        
        log.debug(f"No AniList match above threshold {min_similarity} for {search_name}")
        return "", 0, "", "", 0

    def _is_native_script(self, title: str) -> bool:
        return bool(re.search(r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf\uac00-\ud7af]', title))