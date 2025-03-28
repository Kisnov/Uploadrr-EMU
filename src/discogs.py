from typing import Any, Dict, Optional

import httpx

from src.console import log

class DiscogsAPI:
    BASE_URL = "https://api.discogs.com/releases/"

    def __init__(self, user_agent: str):
        self.user_agent = user_agent

    async def fetch_discogs_release(self, discogs_id: str) -> Optional[Dict[str, Any]]:
        """Fetch release metadata from Discogs using the release ID."""
        url = f"{self.BASE_URL}{discogs_id}"
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

    def extract_metadata(self, release_data: Dict[str, Any], meta: Dict[str, Any]) -> None:
        """Extract relevant metadata from Discogs release data and populate meta."""
        genres = release_data.get("genres", [])
        styles = release_data.get("styles", [])
        
        new_keywords = ", ".join(genres + styles).replace(' & ', ' ')
        if new_keywords:
            log.debug(f"Extracted keywords: {new_keywords}")
            if meta.get('keywords'):
                meta['keywords'] += ", " + new_keywords
            else:
                meta['keywords'] = new_keywords

        if not meta.get('tracklist'):
            tracklist = release_data.get("tracklist", [])
            if tracklist:
                meta['tracklist'] = {}
                for track in tracklist:
                    position = track.get("position", "")
                    
                    if '-' in position:
                        disc_number, track_number = position.split('-', 1)
                        disc_number = f"Disc {disc_number}"
                    else:
                        disc_number = "Disc 1"
                        track_number = position
                    
                    if disc_number not in meta['tracklist']:
                        meta['tracklist'][disc_number] = {}

                    track_title = track.get("title", "Unknown Track")
                    duration = track.get("duration", "")
                    padding = str(track_number).zfill(len(str(len(tracklist))))
                    
                    key = f"{padding}. {track_title}"
                    meta['tracklist'][disc_number][key] = duration
                log.debug(f"Extracted tracklist with {len(tracklist)} tracks")

        if not meta.get('artist'):
            meta['artist'] = ", ".join(artist.get("name", "") 
                                     for artist in release_data.get("artists", []))

        if not meta.get('album'):
            meta['album'] = release_data.get("title", "")

        if not meta.get('year'):
            meta['year'] = release_data.get("year", None)

        if not meta.get('discogs_id'):
            meta['discogs_id'] = release_data.get("id", None)

    async def fetch_master_data(self, master_id: str) -> Optional[Dict[str, Any]]:
        """Fetch master release metadata from Discogs using the master ID."""
        url = f"https://api.discogs.com/masters/{master_id}"
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