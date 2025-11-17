from typing import Any, Dict, Optional

import httpx

from src.utils.console import log


class DeezerHandler:
    BASE_URL = "https://api.deezer.com/"

    def __init__(self, user_agent: str):
        self.user_agent = user_agent

    async def fetch_album(self, album_id: int) -> Optional[Dict[str, Any]]:
        """Fetch album metadata from Deezer using the album ID."""
        url = f"{self.BASE_URL}album/{album_id}"
        headers = {"User-Agent": self.user_agent}

        try:
            async with httpx.AsyncClient() as client:
                log.debug(f"Fetching album data for ID: {album_id}")
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            log.error(f"Deezer API Error: {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            log.error(f"Network error while fetching album {album_id}: {str(e)}")
            return None

    async def fetch_album_by_upc(self, upc: str) -> Optional[Dict[str, Any]]:
        """Fetch album metadata from Deezer using the UPC (barcode)."""
        url = f"{self.BASE_URL}album/upc:{upc}"
        headers = {"User-Agent": self.user_agent}

        try:
            async with httpx.AsyncClient() as client:
                log.debug(f"Fetching album data for UPC: {upc}")
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            log.error(f"Deezer API Error: {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            log.error(f"Network error while fetching album by UPC {upc}: {str(e)}")
            return None

    async def search_albums(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Search for albums on Deezer."""
        url = f"{self.BASE_URL}search/album"
        params = {"q": query, "limit": limit}
        headers = {"User-Agent": self.user_agent}

        try:
            async with httpx.AsyncClient() as client:
                log.debug(f"Searching albums with query: {query}")
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            log.error(f"Deezer API Error: {e.response.status_code}")
            return {"data": []}
        except httpx.RequestError as e:
            log.error(f"Network error while searching albums: {str(e)}")
            return {"data": []}

    async def search_albums_filtered(self, query: str, track_count: int, limit: int = 10) -> Optional[Dict[str, Any]]:
        """Search for albums and filter by track count, returning the best match."""
        search = await self.search_albums(query, limit)
        candidates = search.get("data", [])
        if not candidates:
            return None

        best = next((c for c in candidates if c["nb_tracks"] == track_count), candidates[0])
        return await self.fetch_album(best["id"])
