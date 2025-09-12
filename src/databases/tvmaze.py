from typing import Optional, Tuple

import httpx

from src.utils.console import log


async def search_tvmaze(filename: str, year: Optional[str], imdbID: Optional[str], tvdbID: Optional[str]) -> Tuple[int, str, int]:
    """Search TVmaze for show details.

    Args:
        filename: The name of the show to search for.
        year: The premiere year to filter results (optional).
        imdbID: IMDb ID for lookup (optional).
        tvdbID: TVDB ID for lookup (optional).

    Returns:
        Tuple containing TVmaze ID, IMDb ID, and TVDB ID.
    """
    tvdbID = int(tvdbID or 0)
    tvmazeID = 0
    imdbID = imdbID or "0"

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        url, params = _build_tvmaze_request(filename, imdbID, tvdbID)
        try:
            response = await client.get(url, params=params)
            # 404 is expected for non-tv, don't report
            if response.status_code != 404:
                response.raise_for_status()
            else:
                log.debug(f"TVmaze lookup returned 404 for {params}, likely a movie or invalid ID")
                return tvmazeID, imdbID, tvdbID
            data = response.json() if response.status_code != 404 else None

            if data:
                # Pass the final URL (after redirect) to determine parsing logic
                show = _parse_tvmaze_response(data, year, str(response.url))
                if show:
                    tvmazeID = show.get("id", 0)
                    if int(imdbID) == 0:
                        imdbID = str(show.get("externals", {}).get("imdb", imdbID)).replace("tt", "")
                    if tvdbID == 0:
                        tvdbID = show.get("externals", {}).get("tvdb", tvdbID) or 0
            else:
                log.debug(f"No results found for TVmaze query: {url} with params {params}")
        except httpx.HTTPStatusError as e:
            log.error(f"TVmaze API error: {e.response.status_code} - {e.response.text}")
            log.debug(f"Response headers: {e.response.headers}")
        except httpx.RequestError as e:
            log.error(f"TVmaze request failed: {str(e)}")

    return tvmazeID, imdbID, tvdbID


def _build_tvmaze_request(filename: str, imdbID: str, tvdbID: int) -> Tuple[str, dict]:
    if tvdbID:
        return "https://api.tvmaze.com/lookup/shows", {"thetvdb": tvdbID}
    if int(imdbID) != 0:
        return "https://api.tvmaze.com/lookup/shows", {"imdb": f"tt{imdbID}"}
    return "https://api.tvmaze.com/search/shows", {"q": filename}


def _parse_tvmaze_response(resp: dict, year: Optional[str], final_url: str) -> Optional[dict]:
    # Handle direct show responses (from /lookup/shows or /shows/{id})
    if final_url.endswith("lookup/shows") or final_url.startswith("https://api.tvmaze.com/shows/"):
        return resp if resp else None

    # Handle search results (list of shows)
    if not isinstance(resp, list):
        return None

    if year:
        for each in resp:
            premier_date = each.get("show", {}).get("premiered", "")
            if premier_date and premier_date.startswith(str(year)):
                return each.get("show")
    return resp[0].get("show") if resp else None
