from typing import Any, Dict, Optional

import httpx

from src.utils.console import log


class DiscogsHandler:
    BASE_URL = "https://api.discogs.com"

    def __init__(self, user_agent: str):
        self.user_agent = user_agent

    async def fetch_discogs_release(self, discogs_id: str) -> Optional[Dict[str, Any]]:
        """Fetch release metadata from Discogs using the release ID."""
        url = f"{self.BASE_URL}/releases/{discogs_id}"
        headers = {"User-Agent": self.user_agent}

        try:
            async with httpx.AsyncClient() as client:
                log.debug(f"Fetching release data for ID: {discogs_id}")
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            log.error(f"Discogs API Error: {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            log.error(f"Network error while fetching release {discogs_id}: {str(e)}")
            return None

    def extract_barcode(self, release_data: Dict[str, Any]) -> Optional[str]:
        """Extract barcode from Discogs release data."""
        identifiers = release_data.get("identifiers", [])
        log.debug(f"Extracting barcode from {len(identifiers)} identifiers")

        for identifier in identifiers:
            if identifier.get("type") == "Barcode":
                desc = identifier.get("description", "").lower()
                value = identifier.get("value")
                if desc == "scanned":
                    log.debug(f"Found scanned barcode: {value}")
                    return value
                elif desc == "text":
                    cleaned_value = value.replace(" ", "")
                    log.debug(f"Found text barcode: {cleaned_value}")
                    return cleaned_value
        log.debug("No suitable barcode found")
        return None

    async def fetch_master_data(self, master_id: str) -> Optional[Dict[str, Any]]:
        """Fetch master release metadata from Discogs using the master ID."""
        url = f"{self.BASE_URL}/masters/{master_id}"
        headers = {"User-Agent": self.user_agent}

        try:
            async with httpx.AsyncClient() as client:
                log.debug(f"Fetching master data for ID: {master_id}")
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            log.error(f"Discogs API Error when fetching master data: {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            log.error(f"Network error while fetching master {master_id}: {str(e)}")
            return None

    async def search_releases(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Search for releases on Discogs."""
        url = f"{self.BASE_URL}/database/search"
        params = {"q": query, "type": "release", "per_page": limit}
        headers = {"User-Agent": self.user_agent}

        try:
            async with httpx.AsyncClient() as client:
                log.debug(f"Searching releases with query: {query}")
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            log.error(f"Discogs API Error: {e.response.status_code}")
            return {"results": []}
        except httpx.RequestError as e:
            log.error(f"Network error while searching releases: {str(e)}")
            return {"results": []}

    async def search_releases_filtered(self, query: str, track_count: int, limit: int = 10) -> Optional[Dict[str, Any]]:
        """Search for releases and filter by track count, returning the best match."""
        search = await self.search_releases(query, limit)
        candidates = search.get("results", [])
        if not candidates:
            return None

        best = None
        for c in candidates:
            if c.get("tracklist"):
                if len(c["tracklist"]) == track_count:
                    best = c
                    break

        if not best:
            best = candidates[0]

        return await self.fetch_discogs_release(best["id"])
