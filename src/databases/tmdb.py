import asyncio
import json
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, Optional, Tuple

import anitopy
import httpx
from aiocache import cached
from guessit import guessit
from rich.prompt import Prompt

from src.args import parse_tmdb_id
from src.progress import ProgressManager
from src.utils.console import console, log

from .anilist import AniListHandler


class TMDbHandler:
    """Handler for TMDb API operations."""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self.semaphore = asyncio.Semaphore(40)  # TMDb limit: ~40 requests per 10 seconds
        self.client = httpx.AsyncClient(base_url="https://api.themoviedb.org/3", timeout=10.0)
        self.api_key = self.config["DEFAULT"].get("tmdb")

    async def rate_limit(self) -> None:
        """Enforce rate limiting for API requests."""
        async with self.semaphore:
            await asyncio.sleep(0.25)

    @cached(ttl=180)
    async def get_tmdb_from_imdb(self, meta: Dict, filename: str) -> Dict:
        """Retrieve TMDb ID using IMDb ID or fallback to title search."""
        if meta.get("tmdb_manual") is not None:
            meta["tmdb"] = meta["tmdb_manual"]
            return meta

        imdb_id = meta["imdb"]
        if not str(imdb_id).lower().startswith("tt"):
            imdb_id = f"tt{imdb_id}"

        await self.rate_limit()
        response = await self.client.get(f"/find/{imdb_id}", params={"api_key": self.api_key, "external_source": "imdb_id"})
        response.raise_for_status()
        info = response.json()

        if info.get("movie_results"):
            meta["category"] = "MOVIE"
            meta["tmdb"] = info["movie_results"][0]["id"]
        elif info.get("tv_results"):
            meta["category"] = "TV"
            meta["tmdb"] = info["tv_results"][0]["id"]
        else:
            title = filename
            year = meta["search_year"]
            console.print(f"[yellow]TMDb failed with IMDb ID {imdb_id}, searching {title}")
            meta = await self.get_tmdb_id(title, year, meta, meta["category"], filename)
            if not meta.get("tmdb") or meta["tmdb"] in ("None", "0"):
                if meta.get("mode", "discord") == "cli":
                    console.print("[yellow]No TMDb entry found")
                    tmdb_id = console.input("Please enter TMDb ID: ")
                    meta["category"], meta["tmdb"] = parse_tmdb_id(tmdb_id, meta.get("category"))
        return meta

    @cached(ttl=180)
    async def get_tmdb_id(
        self, filename: str, search_year: int, meta: Dict, category: str, untouched_filename: str = "", attempted: int = 0
    ) -> Dict:
        """Search for TMDb ID based on filename and year with stricter year matching."""
        log.debug(f"Searching TMDb: filename={filename}, year={search_year}, category={category}, attempt={attempted}")
        try:
            await self.rate_limit()
            endpoint = "/search/movie" if category == "MOVIE" else "/search/tv"
            params = {"api_key": self.api_key, "query": filename, "include_adult": True}
            if search_year:
                params["year" if category == "MOVIE" else "first_air_date_year"] = search_year

            response = await self.client.get(endpoint, params=params)
            response.raise_for_status()
            results = response.json().get("results", [])

            # Filter results by year range (±4 years)
            if search_year not in (None, ""):
                year_range = range(int(search_year) - 4, int(search_year) + 5)
                results = [r for r in results if self._get_year_from_result(r, category) in year_range]

            if meta.get("tmdb_manual") is not None:
                meta["tmdb"] = meta["tmdb_manual"]
            elif results:
                meta["tmdb"] = results[0]["id"]
                meta["category"] = category
                meta["title"] = results[0].get("title" if category == "MOVIE" else "name", filename)
            else:
                raise IndexError
        except IndexError:
            return await self._fallback_tmdb_search(filename, search_year, meta, category, untouched_filename, attempted)
        return meta

    def _get_year_from_result(self, result: Dict, category: str) -> int:
        """Extract year from TMDb result."""
        date_key = "release_date" if category == "MOVIE" else "first_air_date"
        date_str = result.get(date_key, "")
        try:
            return int(date_str.split("-")[0]) if date_str else 0
        except (ValueError, IndexError):
            return 0

    async def _fallback_tmdb_search(
        self, filename: str, search_year: int, meta: Dict, category: str, untouched_filename: str, attempted: int
    ) -> Dict:
        """Fallback search logic for TMDb ID"""  # TODO for adultt strip out common pefixes. SCORE Presents etc
        try:
            await self.rate_limit()
            endpoint = "/search/movie" if category == "MOVIE" else "/search/tv"
            response = await self.client.get(endpoint, params={"api_key": self.api_key, "query": filename, "include_adult": True})
            response.raise_for_status()
            results = response.json().get("results", [])

            # Apply year filtering
            if search_year not in (None, ""):
                year_range = range(int(search_year) - 4, int(search_year) + 5)
                results = [r for r in results if self._get_year_from_result(r, category) in year_range]

            if results:
                meta["tmdb"] = results[0]["id"]
                meta["category"] = category
            else:
                raise IndexError
        except IndexError:
            next_category = "TV" if category == "MOVIE" else "MOVIE"
            if attempted <= 1:
                return await self.get_tmdb_id(filename, search_year, meta, next_category, untouched_filename, attempted + 1)
            elif attempted == 2:
                parsed_title = anitopy.parse(guessit(untouched_filename, {"excludes": ["country", "language"]})["title"])[
                    "anime_title"
                ]
                return await self.get_tmdb_id(parsed_title, search_year, meta, meta["category"], untouched_filename, attempted + 1)

            if not meta.get("tmdb"):
                console.print(f"[red]No reasonable TMDb match for {filename} found.")
                if meta.get("unattended"):
                    meta["tmdb_not_found"] = True
                else:
                    progress = ProgressManager.get_instance()
                    with progress.pause_for_interaction():
                        tmdb_id = Prompt.ask("Please enter TMDb ID (tv/12345 or movie/12345): ")
                    meta["category"], meta["tmdb"] = parse_tmdb_id(tmdb_id, meta.get("category"))
                    meta["tmdb_manual"] = meta["tmdb"]
        return meta

    async def tmdb_other_meta(self, meta: Dict) -> Dict:
        """Fetch additional metadata from TMDb."""
        if meta["tmdb"] == "0":
            meta = await self._handle_missing_tmdb(meta)
            if meta["tmdb"] == "0":
                return meta

        max_retries = 4
        original_category = meta["category"]
        for attempt in range(max_retries):
            try:
                result = await self._fetch_tmdb_metadata(meta)
                return result
            except httpx.HTTPStatusError as e:
                await self._handle_tmdb_error(e, meta, attempt, max_retries)
            except Exception as e:
                log.error(f"Unexpected error in attempt {attempt + 1}: {str(e)}", exc_info=True)
                if attempt == 0:
                    log.debug(f"Switching category from {meta['category']} to {'TV' if meta['category'] == 'MOVIE' else 'MOVIE'}")
                    meta["category"] = "TV" if meta["category"] == "MOVIE" else "MOVIE"
                if attempt == max_retries - 1:
                    log.warning(f"Failed after {max_retries} attempts, returning meta: {meta}")
                    meta["category"] = original_category  # Reset on final failure
                    return meta
                await asyncio.sleep(1)
        meta["category"] = original_category
        return meta

    async def _fetch_tmdb_metadata(self, meta: Dict) -> Dict:
        """Core logic to fetch TMDb metadata."""
        await self.rate_limit()
        endpoint = f"/{'movie' if meta['category'] == 'MOVIE' else 'tv'}/{meta['tmdb']}"
        response = await self.client.get(
            endpoint,
            params={
                "api_key": self.api_key,
                "append_to_response": "external_ids,videos,images,credits,keywords",
                "include_adult": True,
            },
        )
        response.raise_for_status()
        data = response.json()
        log.ddebug(f"TMDb response for ID {meta['tmdb']}:\n {json.dumps(data, indent=2, sort_keys=True)}")

        from .imdb import IMDbHandler

        imdb_task = asyncio.create_task(IMDbHandler().find_aka(meta.get("imdb_id", "0")))
        aka, original_language = await imdb_task

        meta.update(await self._parse_tmdb_data(data, meta, aka, original_language))
        meta.update(await self._process_tmdb_images(data))

        if not meta.get("anime", False):
            meta["mal_id"], meta["aka"], meta["anime"] = await AniListHandler().get_anime(data, meta)

        meta = await self._post_process_metadata(meta)
        return meta

    async def _parse_tmdb_data(self, data: Dict, meta: Dict, aka: str, original_language: str) -> Dict:
        """Parse TMDb response data into metadata."""
        date_key = "release_date" if meta["category"] == "MOVIE" else "first_air_date"
        release_date = data.get(date_key)
        year, full_date = self._parse_date(release_date, meta.get("search_year", ""))
        title_or_name = "title" if meta["category"] == "MOVIE" else "name"
        default_title = data.get(title_or_name, meta.get("title", ""))

        common_data = {
            "title": default_title,
            "year": year,
            "full_date": full_date,
            "imdb_id": self._normalize_imdb_id(data.get("external_ids", {}).get("imdb_id", meta.get("imdb", "0"))),
            "tvdb_id": data.get("external_ids", {}).get("tvdb_id", meta.get("tvdb_id", "0")) or "0",
            "adult": data["adult"],
            "overview": data["overview"],
            "youtube": self._get_youtube_trailer(data),
            "aka": aka,
            "original_language": original_language or data["original_language"],
            "original_title": data.get("original_title" if meta["category"] == "MOVIE" else "original_name", default_title),
            "keywords": ", ".join(
                kw["name"].replace(",", " ")
                for kw in data.get("keywords", {}).get("keywords", []) or data.get("keywords", {}).get("results", [])
            ),
            "genres": ", ".join(g["name"].replace(",", " ") for g in data.get("genres", [])),
            "tmdb_directors": [
                c.get("original_name", c.get("name")) for c in data.get("credits", {}).get("crew", []) if c.get("job") == "Director"
            ],
        }

        if meta["category"] == "MOVIE":
            common_data["tmdb_type"] = "Movie"
            common_data["runtime"] = data.get("runtime", 60)
        else:
            common_data["tmdb_type"] = data.get("type", "Scripted")
            common_data["runtime"] = (data.get("episode_run_time") or [60])[0]

        return common_data

    def _parse_date(self, release_date: str, fallback_year: str) -> Tuple[str, str]:
        """Parse release date and return year and full date."""
        if release_date:
            try:
                date_obj = datetime.strptime(release_date, "%Y-%m-%d")
                return str(date_obj.year), date_obj.strftime("%Y-%m-%d")
            except ValueError:
                console.print("[yellow]TMDb date parsing failed, using year from filename")
                return fallback_year, ""
        console.print("[yellow]TMDb lacks release date, using year from filename")
        return fallback_year, ""

    async def _process_tmdb_images(self, data: Dict) -> Dict:
        """Process TMDb image data (poster, logo, backdrop)."""
        poster_path = data.get("poster_path", "")
        logos = data.get("images", {}).get("logos", [])
        backdrops = data.get("images", {}).get("backdrops", [])

        first_logo = next((logo for logo in logos if logo.get("iso_639_1") == "en"), None) or (logos[0] if logos else None)
        first_backdrop = next((bd for bd in backdrops if bd.get("iso_639_1") == "en"), None) or (backdrops[0] if backdrops else None)

        return {
            "poster": f"https://image.tmdb.org/t/p/original{poster_path}" if poster_path else "",
            "logo": f"https://image.tmdb.org/t/p/original{first_logo['file_path']}" if first_logo else None,
            "backdrop": f"https://image.tmdb.org/t/p/original{first_backdrop['file_path']}" if first_backdrop else None,
        }

    async def _post_process_metadata(self, meta: Dict) -> Dict:
        """Post-process metadata (e.g., AKA handling)."""
        title = meta.get("title", "") or ""
        aka = meta.get("aka", "") or ""
        if aka and len(aka) > 5:  # Check length before slicing
            difference = SequenceMatcher(None, title.lower(), aka[5:].lower()).ratio()
            if difference >= 0.9 or aka[5:].strip() == "" or aka[5:].strip().lower() in title.lower():
                meta["aka"] = ""
        if f"({meta.get('year', '')})" in aka:
            meta["aka"] = aka.replace(f"({meta.get('year', '')})", "").strip()
        return meta

    async def _handle_missing_tmdb(self, meta: Dict) -> Dict:
        """Handle case where TMDb ID is missing."""
        try:
            title = guessit(meta["path"], {"excludes": ["country", "language"]})["title"].lower().split("aka")[0]
            meta = await self.get_tmdb_id(title, meta["search_year"], meta, meta["category"])
            if meta["tmdb"] == "0":
                meta = await self.get_tmdb_id(title, "", meta, meta["category"])
        except Exception as e:
            log.error(f"Failed to find TMDb entry: {str(e)}")
            if meta.get("mode", "discord") == "cli":
                console.print("[bold red]Unable to find TMDb entry. Exiting.")
                exit()
            else:
                console.print("[bold red]Unable to find TMDb entry")
        return meta

    async def _handle_tmdb_error(self, error: httpx.HTTPStatusError, meta: Dict, attempt: int, max_retries: int) -> None:
        """Handle TMDb HTTP errors with retries."""
        log.error(f"HTTP Error on attempt {attempt + 1}: {str(error)}")
        if attempt == max_retries - 1:
            console.print(f"[red]Failed after {max_retries} attempts: {str(error)}")
        elif error.response.status_code == 429:  # Too Many Requests
            await asyncio.sleep(2**attempt)
        elif error.response.status_code == 404:
            console.print("[red]Invalid TMDb ID.")
            tmdb_id = Prompt.ask("Please enter TMDb ID (tv/12345 or movie/12345): ")
            meta["category"], meta["tmdb"] = parse_tmdb_id(tmdb_id, meta.get("category"))
            meta["tmdb_manual"] = meta["tmdb"]
        else:
            await asyncio.sleep(1)

    def _normalize_imdb_id(self, imdb_id: str) -> str:
        """Normalize IMDb ID format."""
        return str(int(imdb_id.replace("tt", ""))).zfill(7) if imdb_id and imdb_id != "0" else "0"

    def _get_youtube_trailer(self, data: Dict) -> Optional[str]:
        """Extract YouTube trailer key from TMDb data."""
        trailer = next(
            (v["key"] for v in data.get("videos", {}).get("results", []) if v.get("site") == "YouTube" and v.get("type") == "Trailer"),
            None,
        )
        if not trailer:
            log.debug(f"No YouTube trailer found for TMDb ID {data.get('id')}")
        return trailer

    async def daily_to_tmdb_season_episode(self, tmdbid: str, date: str) -> Tuple[str, str]:
        """Map a date to season and episode for a TV show."""
        await self.rate_limit()
        response = await self.client.get(f"/tv/{tmdbid}", params={"api_key": self.api_key})
        response.raise_for_status()
        seasons = response.json().get("seasons", [])

        season, episode = "1", "1"
        date = datetime.fromisoformat(str(date))
        for s in seasons:
            air_date = datetime.fromisoformat(s["air_date"])
            if air_date <= date:
                season = str(s["season_number"])

        await self.rate_limit()
        season_response = await self.client.get(f"/tv/{tmdbid}/season/{season}", params={"api_key": self.api_key})
        season_response.raise_for_status()
        episodes = season_response.json().get("episodes", [])

        for ep in episodes:
            if str(ep["air_date"]) == str(date.date()):
                episode = str(ep["episode_number"])
                break
        else:
            console.print(f"[yellow]Unable to map date ({date}) to Season/Episode")
        return season, episode
