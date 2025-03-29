from datetime import datetime
import asyncio
from asyncio import Semaphore
from difflib import SequenceMatcher
import os
import re
import urllib.parse

import httpx
import requests
import anitopy
from imdb import Cinemagoer
from guessit import guessit
from aiocache import cached
from rich.prompt import Prompt

from src.args import Args
from src.console import console, log


class TMDbHandler:
    TITLE_CLEANER = re.compile(r"[^0-9a-zA-Z\[\]]+")
    JAPANESE_CLEANER = re.compile(r"[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]+ (?=[A-Za-z ]+–)", re.U)

    def __init__(self, config=None):
        self.config = config or {}
        self.semaphore = Semaphore(40)  # TMDb limit: ~40 requests per 10 seconds
        self.client = httpx.AsyncClient(base_url="https://api.themoviedb.org/3", timeout=10.0)
        self.api_key = self.config['DEFAULT'].get('tmdb_api')

    async def rate_limit(self):
        async with self.semaphore:
            await asyncio.sleep(0.25)

    @cached(ttl=180) 
    async def get_tmdb_from_imdb(self, meta, filename):
        if meta.get('tmdb_manual') is not None:
            meta['tmdb'] = meta['tmdb_manual']
            return meta
        imdb_id = meta['imdb']
        if str(imdb_id)[:2].lower() != "tt":
            imdb_id = f"tt{imdb_id}"

        await self.rate_limit()
        response = await self.client.get(
            "/find/" + imdb_id,
            params={
                "api_key": self.api_key,
                "external_source": "imdb_id"
            }
        )
        response.raise_for_status()
        info = response.json()

        if info.get('movie_results', []):
            meta['category'] = "MOVIE"
            meta['tmdb'] = info['movie_results'][0]['id']
        elif info.get('tv_results', []):
            meta['category'] = "TV"
            meta['tmdb'] = info['tv_results'][0]['id']
        else:
            # Fallback to search by title/year if IMDb lookup fails
            title = filename
            year = meta['search_year']
            console.print(f"[yellow]TMDb was unable to find anything with IMDb ID {imdb_id}, searching TMDb for {title}")
            meta = await self.get_tmdb_id(title, year, meta, meta['category'], filename)
            if meta.get('tmdb') in ('None', '', None, 0, '0'):
                if meta.get('mode', 'discord') == 'cli':
                    console.print('[yellow]Unable to find a matching TMDb entry')
                    tmdb_id = console.input("Please enter TMDb ID: ")
                    parser = Args(config=self.config)
                    meta['category'], meta['tmdb'] = parser.parse_tmdb_id(id=tmdb_id, category=meta.get('category'))
        return meta

    @cached(ttl=180)
    async def get_tmdb_id(self, filename, search_year, meta, category, untouched_filename="", attempted=0):
        if meta['is_disc'] == 'BDMV' and meta['discs'][0]['bdinfo'].get('title') not in (None, ''):
            title = meta['discs'][0]['bdinfo']['title']
            filename = re.sub(r'Blu-ray™|-\s*|Vol\.\s*\d+', '', title).strip()
        log.debug(f"Searching TMDb: filename={filename}, year={search_year}, category={category}, attempt={attempted}")
        try:
            await self.rate_limit()
            endpoint = "/search/movie" if category == "MOVIE" else "/search/tv"
            params = {
                "api_key": self.api_key,
                "query": filename,
                "include_adult": True
            }
            if search_year:
                params["year" if category == "MOVIE" else "first_air_date_year"] = search_year

            response = await self.client.get(endpoint, params=params)
            response.raise_for_status()
            results = response.json().get('results', [])

            if meta.get('tmdb_manual') is not None:
                meta['tmdb'] = meta['tmdb_manual']
            elif results:
                meta['tmdb'] = results[0]['id']
                meta['category'] = category
            else:
                raise IndexError
        except IndexError:
            try:
                await self.rate_limit()
                endpoint = "/search/movie" if category == "MOVIE" else "/search/tv"
                response = await self.client.get(endpoint, params={
                    "api_key": self.api_key,
                    "query": filename,
                    "include_adult": True
                })
                response.raise_for_status()
                results = response.json().get('results', [])
                if results:
                    meta['tmdb'] = results[0]['id']
                    meta['category'] = category
                else:
                    raise IndexError
            except IndexError:
                if category == "MOVIE":
                    category = "TV"
                else:
                    category = "MOVIE"

                if attempted <= 1:
                    attempted += 1
                    meta = await self.get_tmdb_id(filename, search_year, meta, category, untouched_filename, attempted)
                elif attempted == 2:
                    attempted += 1
                    parsed_title = anitopy.parse(guessit(untouched_filename, {"excludes": ["country", "language"]})['title'])['anime_title']
                    meta = await self.get_tmdb_id(parsed_title, search_year, meta, meta['category'], untouched_filename, attempted)

                if meta.get('tmdb') in (None, ""):
                    console.print(f"[red]Unable to find TMDb match for {filename}")
                    if meta.get('unattended'):
                        meta['tmdb_not_found'] = True
                        return meta
                    else:
                        tmdb_id = Prompt.ask("Please enter TMDb ID in this format: tv/12345 or movie/12345\n")
                        parser = Args(config=self.config)
                        meta['category'], meta['tmdb'] = parser.parse_tmdb_id(id=tmdb_id, category=meta.get('category'))
                        meta['tmdb_manual'] = meta['tmdb']
        return meta

    async def tmdb_other_meta(self, meta):
        if meta['tmdb'] == "0":
            try:
                title = guessit(meta['path'], {"excludes": ["country", "language"]})['title'].lower().split('aka')[0]
                meta = await self.get_tmdb_id(title, meta['search_year'], meta, meta['category'])
                if meta['tmdb'] == "0":
                    meta = await self.get_tmdb_id(title, "", meta, meta['category'])
            except Exception as e:
                log.error(f"Failed to find TMDb entry: {str(e)}")
                if meta.get('mode', 'discord') == 'cli':
                    console.print("[bold red]Unable to find TMDb entry. Exiting.")
                    exit()
                else:
                    console.print("[bold red]Unable to find TMDb entry")
                    return meta

        max_retries = 3
        for attempt in range(max_retries):
            try:
                await self.rate_limit()
                endpoint = "/movie" if meta['category'] == "MOVIE" else "/tv"
                response = await self.client.get(
                    f"{endpoint}/{meta['tmdb']}",
                    params={
                        "api_key": self.api_key,
                        "append_to_response": "external_ids,videos,images,credits,keywords",
                        "include_adult": True
                    }
                )
                response.raise_for_status()
                data = response.json()

                imdb_task = asyncio.create_task(IMDbHandler().get_imdb_aka(meta.get('imdb_id', '0')))
                aka, original_language = await imdb_task

                if meta['category'] == "MOVIE":
                    release_date = data.get('release_date')
                    full_date = ""
                    year = meta['search_year']
                    if release_date:
                        try:
                            date_obj = datetime.strptime(release_date, '%Y-%m-%d')
                            year = date_obj.year
                            full_date = date_obj.strftime('%Y-%m-%d')
                        except Exception:
                            console.print('[yellow]TMDb date parsing failed, using year from filename')
                    else:
                        console.print('[yellow]TMDb does not have a release date, using year from filename')

                    meta.update({
                        'title': data['title'],
                        'year': year,
                        'full_date': full_date,
                        'imdb_id': self._normalize_imdb_id(data.get('external_ids', {}).get('imdb_id', meta.get('imdb', '0'))),
                        'tvdb_id': data.get('external_ids', {}).get('tvdb_id', meta.get('tvdb_id', '0')) or '0',
                        'adult': data['adult'],
                        'overview': data['overview'],
                        'tmdb_type': 'Movie',
                        'runtime': data.get('runtime', 60)
                    })

                elif meta['category'] == "TV":
                    first_air_date = data.get('first_air_date')
                    full_date = ""
                    year = meta['search_year']
                    if first_air_date:
                        try:
                            date_obj = datetime.strptime(first_air_date, '%Y-%m-%d')
                            year = date_obj.year
                            full_date = date_obj.strftime('%Y-%m-%d')
                        except Exception:
                            console.print('[yellow]TMDb date parsing failed, using year from filename')
                    else:
                        console.print('[yellow]TMDb does not have a release date, using year from filename')

                    runtime = data.get('episode_run_time', [60])
                    meta.update({
                        'title': data['name'],
                        'year': year,
                        'full_date': full_date,
                        'imdb_id': self._normalize_imdb_id(data.get('external_ids', {}).get('imdb_id', meta.get('imdb', '0'))),
                        'tvdb_id': data.get('external_ids', {}).get('tvdb_id', meta.get('tvdb_id', '0')) or '0',
                        'adult': data['adult'],
                        'overview': data['overview'],
                        'tmdb_type': data.get('type', 'Scripted'),
                        'runtime': runtime[0] if runtime else 60
                    })

                # Trailer
                meta['youtube'] = next((v['key'] for v in data.get('videos', {}).get('results', [])
                                        if v.get('site') == 'YouTube' and v.get('type') == 'Trailer'), None)
                if not meta['youtube']:
                    log.debug(f"No YouTube trailer found for TMDb ID {meta['tmdb']}")

                # Additional metadata
                meta.update({
                    'aka': aka,
                    'original_language': original_language or data['original_language'],
                    'original_title': data.get('original_title', meta['title']) if meta['category'] == "MOVIE" else data.get('original_name', meta['title']),
                    'keywords': ', '.join(kw['name'].replace(',', ' ') for kw in data.get('keywords', {}).get('keywords', []) or data.get('keywords', {}).get('results', [])),
                    'genres': ', '.join(g['name'].replace(',', ' ') for g in data.get('genres', [])),
                    'tmdb_directors': [c.get('original_name', c.get('name'))
                                       for c in data.get('credits', {}).get('crew', [])
                                       if c.get('job') == 'Director']
                })

                if not meta.get('anime', False):
                    meta['mal_id'], meta['aka'], meta['anime'] = await self.get_anime(data, meta)

                # Poster, Logo, and Backdrop
                poster_path = data.get('poster_path', '')
                meta['poster'] = f"https://image.tmdb.org/t/p/original{poster_path}" if poster_path else ""
                logos = data.get('images', {}).get('logos', [])
                first_logo = next((logo for logo in logos if logo.get('iso_639_1') == 'en'), None) or (logos[0] if logos else None)
                meta['logo'] = f"https://image.tmdb.org/t/p/original{first_logo['file_path']}" if first_logo else None
                backdrops = data.get('images', {}).get('backdrops', [])
                first_backdrop = next((bd for bd in backdrops if bd.get('iso_639_1') == 'en'), None) or (backdrops[0] if backdrops else None)
                meta['backdrop'] = f"https://image.tmdb.org/t/p/original{first_backdrop['file_path']}" if first_backdrop else None

                # Post-processing
                difference = SequenceMatcher(None, meta['title'].lower(), meta['aka'][5:].lower()).ratio()
                if difference >= 0.9 or meta['aka'][5:].strip() == "" or meta['aka'][5:].strip().lower() in meta['title'].lower():
                    meta['aka'] = ""
                if f"({meta['year']})" in meta['aka']:
                    meta['aka'] = meta['aka'].replace(f"({meta['year']})", "").strip()

                break  # Success, exit retry loop

            except httpx.HTTPStatusError as e:
                log.error(f"HTTP Error on attempt {attempt + 1}: {str(e)}")
                if attempt == max_retries - 1:
                    console.print(f"[red]Failed after {max_retries} attempts: {str(e)}")
                    return meta
                elif e.response.status_code == 429:  # Too Many Requests
                    await asyncio.sleep(2 ** attempt)
                elif e.response.status_code == 404:
                    console.print("[red]The TMDb ID you entered could not be found.")
                    tmdb_id = Prompt.ask("Please enter TMDb ID in this format: tv/12345 or movie/12345\n")
                    parser = Args(config=self.config)
                    meta['category'], meta['tmdb'] = parser.parse_tmdb_id(id=tmdb_id, category=meta.get('category'))
                    meta['tmdb_manual'] = meta['tmdb']
                else:
                    await asyncio.sleep(1)
            except Exception as e:
                log.error(f"Unexpected error: {str(e)}")
                if attempt == max_retries - 1:
                    return meta
                await asyncio.sleep(1)
        return meta

    def _normalize_imdb_id(self, imdb_id):
        return str(int(imdb_id.replace('tt', ''))).zfill(7) if imdb_id and imdb_id != "0" else '0'

    async def get_anime(self, response, meta):
        tmdb_name = meta['title']
        alt_name = meta.get('aka', "")
        anime = False
        animation = False
        for genre in response.get('genres', []):
            if genre['id'] == 16:
                animation = True
        if response['original_language'] == 'ja' and animation:
            romaji, mal_id, _, _, _ = asyncio.run(self.get_romaji(tmdb_name, meta.get('mal', None)))
            alt_name = f" AKA {romaji}"
            anime = True
        else:
            mal_id = 0
        if meta.get('mal_id', 0) != 0:
            mal_id = meta['mal_id']
        if meta.get('mal') not in ('0', 0, None):
            mal_id = meta['mal']
        return mal_id, alt_name, anime

    async def get_romaji(self, tmdb_name, mal):
        if mal is None:
            mal = 0
            tmdb_name = self.TITLE_CLEANER.sub("", tmdb_name.lower().replace(" ", "").replace("-", "").replace("The Movie", ""))
            tmdb_name = ' '.join(tmdb_name.split())
            query = '''
                query ($search: String) {
                    Page (page: 1) {
                        pageInfo {
                            total
                        }
                    media (search: $search, type: ANIME, sort: SEARCH_MATCH) {
                        id
                        idMal
                        title {
                            romaji
                            english
                            native
                        }
                        seasonYear
                        episodes
                    }
                }
            }
            '''
            variables = {'search': tmdb_name}
        else:
            query = '''
                query ($search: Int) {
                    Page (page: 1) {
                        pageInfo {
                            total
                        }
                    media (idMal: $search, type: ANIME, sort: SEARCH_MATCH) {
                        id
                        idMal
                        title {
                            romaji
                            english
                            native
                        }
                        seasonYear
                        episodes
                    }
                }
            }
            '''
            variables = {'search': mal}

        url = 'https://graphql.anilist.co'
        await self.rate_limit()
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json={'query': query, 'variables': variables})
            response.raise_for_status()
            json_data = response.json()
            media = json_data['data']['Page']['media']

        if media:
            result = {'title': {}}
            difference = 0
            search_name = self.TITLE_CLEANER.sub("", tmdb_name.lower())
            for anime in media:
                for title in anime['title'].values():
                    if title:
                        title = self.JAPANESE_CLEANER.sub("", title.lower())
                        diff = SequenceMatcher(None, title, search_name).ratio()
                        if diff >= difference:
                            result = anime
                            difference = diff

            romaji = result['title'].get('romaji', result['title'].get('english', ""))
            mal_id = result.get('idMal', 0)
            eng_title = result['title'].get('english', result['title'].get('romaji', ""))
            season_year = result.get('seasonYear', "")
            episodes = result.get('episodes', 0)
        else:
            romaji = eng_title = season_year = ""
            episodes = mal_id = 0

        if mal_id in (None, 0):
            mal_id = mal
        if not episodes:
            episodes = 0
        return romaji, mal_id, eng_title, season_year, episodes

    async def daily_to_tmdb_season_episode(self, tmdbid, date):
        await self.rate_limit()
        response = await self.client.get(
            f"/tv/{tmdbid}",
            params={"api_key": self.api_key}
        )
        response.raise_for_status()
        seasons = response.json().get('seasons', [])
        
        season, episode = '1', '1'
        date = datetime.fromisoformat(str(date))
        for s in seasons:
            air_date = datetime.fromisoformat(s['air_date'])
            if air_date <= date:
                season = str(s['season_number'])
        
        await self.rate_limit()
        season_response = await self.client.get(
            f"/tv/{tmdbid}/season/{season}",
            params={"api_key": self.api_key}
        )
        season_response.raise_for_status()
        episodes = season_response.json().get('episodes', [])
        
        for ep in episodes:
            if str(ep['air_date']) == str(date.date()):
                episode = str(ep['episode_number'])
                break
        else:
            console.print(f"[yellow]Unable to map the date ([bold yellow]{str(date)}[/bold yellow]) to a Season/Episode number")
        return season, episode


class IMDbHandler:
    def __init__(self):
        self.ia = Cinemagoer()

    async def get_imdb_info(self, imdb_id, meta):
        imdb_info = {}
        imdb_id = str(imdb_id).replace('tt', '')  # Normalize ID
        if int(imdb_id) != 0:
            try:
                info = self.ia.get_movie(imdb_id)
                self.ia.update(info, ['technical'])
                imdb_info['title'] = info.get('title', meta['title'])
                imdb_info['year'] = info.get('year', meta['year'])
                imdb_info['aka'] = info.get('original title', info.get('localized title', imdb_info['title'])).replace(' - IMDb', '').replace('\u00ae', '')
                imdb_info['type'] = info.get('kind')
                imdb_info['imdbID'] = info.get('imdbID', f"tt{imdb_id.zfill(7)}")
                imdb_info['runtime'] = info.get('runtimes', ['0'])[0]
                imdb_info['cover'] = info.get('full-size cover url', '').replace(".jpg", "._V1_FMjpg_UX750_.jpg")
                imdb_info['plot'] = info.get('plot', [''])[0]
                imdb_info['genres'] = ', '.join(info.get('genres', []))
                imdb_info['soundmix'] = info.get('sound mix')
                imdb_info['countries_of_origin'] = info.get('countries', [])
                imdb_info['original_language'] = info.get('language codes', [])
                if isinstance(imdb_info['original_language'], list):
                    imdb_info['original_language'] = imdb_info['original_language'][0] if len(imdb_info['original_language']) == 1 else None
                if imdb_info['cover'] in ('', None):
                    imdb_info['cover'] = meta.get('poster', '')
                
                directors = info.get('directors', [])
                imdb_info['directors'] = [f"nm{director.getID()}" for director in directors] if directors else meta.get('tmdb_directors', [])
            except Exception as e:
                log.error(f"Failed to fetch IMDb info for ID {imdb_id}: {str(e)}")
                imdb_info = self._fallback_from_meta(meta)
        else:
            imdb_info = self._fallback_from_meta(meta)
        return imdb_info

    def _fallback_from_meta(self, meta):
        """Fallback to meta data if IMDb fetch fails or ID is invalid."""
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

    async def search_imdb(self, filename, search_year):
        imdb_id = '0'
        try:
            search = self.ia.search_movie(filename)
            for movie in search:
                if filename.lower() in movie.get('title', '').lower() and movie.get('year') == search_year:
                    imdb_id = str(movie.movieID).replace('tt', '')
                    break
        except Exception as e:
            log.error(f"IMDb search failed for {filename} ({search_year}): {str(e)}")
        return imdb_id

    async def imdb_other_meta(self, meta):
        meta['imdb_info'] = await self.get_imdb_info(meta['imdb_id'], meta)
        meta['title'] = meta['imdb_info']['title']
        meta['aka'] = meta['imdb_info']['aka']
        meta['poster'] = meta['imdb_info']['cover']
        meta['original_language'] = meta['imdb_info']['original_language']
        meta['overview'] = meta['imdb_info']['plot']

        if meta['aka']:
            difference = SequenceMatcher(None, meta['title'].lower(), meta['aka'][5:].lower()).ratio()
            if difference >= 0.9 or meta['aka'][5:].strip() == "" or meta['aka'][5:].strip().lower() in meta['title'].lower():
                meta['aka'] = ""
            if f"({meta['year']})" in meta['aka']:
                meta['aka'] = meta['aka'].replace(f"({meta['year']})", "").strip()
        return meta

    async def get_imdb_aka(self, imdb_id):
        if imdb_id == "0":
            return "", None
        try:
            result = self.ia.get_movie(imdb_id.replace('tt', ''))
            original_language = result.get('language codes', [])
            original_language = original_language[0] if len(original_language) == 1 else None
            aka = result.get('original title', result.get('localized title', "")).replace(' - IMDb', '').replace('\u00ae', '')
            if aka:
                aka = f" AKA {aka}"
            return aka, original_language
        except Exception as e:
            log.error(f"Failed to get IMDb AKA for ID {imdb_id}: {str(e)}")
            return "", None


async def search_tvmaze(filename, year, imdbID, tvdbID):
    tvdbID = int(tvdbID)
    tvmazeID = 0
    lookup = False
    show = None
    if imdbID is None:
        imdbID = '0'
    if tvdbID is None:
        tvdbID = 0
    if int(tvdbID) != 0:
        params = {
            "thetvdb" : tvdbID
        }
        url = "https://api.tvmaze.com/lookup/shows"
        lookup = True
    elif int(imdbID) != 0:
        params = {
            "imdb" : f"tt{imdbID}"
        }
        url = "https://api.tvmaze.com/lookup/shows"
        lookup = True
    else:
        params = {
            "q" : filename
        }
        url = "https://api.tvmaze.com/search/shows"
    resp = requests.get(url=url, params=params)
    if resp.ok:
        resp = resp.json()
        if resp is None:
            return tvmazeID, imdbID, tvdbID
        if lookup is True:
            show = resp
        else:
            if year not in (None, ''):
                for each in resp:
                    premier_date = each['show'].get('premiered', '')
                    if premier_date is not None:
                        if premier_date.startswith(str(year)):
                            show = each['show']
            elif len(resp) >= 1:
                show = resp[0]['show']
        if show is not None:
            tvmazeID = show.get('id')
            if int(imdbID) == 0:
                if show.get('externals', {}).get('imdb', '0') is not None:
                    imdbID = str(show.get('externals', {}).get('imdb', '0')).replace('tt', '')
            if int(tvdbID) == 0:
                if show.get('externals', {}).get('tvdb', '0') is not None:
                    tvdbID = show.get('externals', {}).get('tvdb', '0')
    return tvmazeID, imdbID, tvdbID

async def is_scene(video, imdb=None):
    scene = False
    base = os.path.splitext(os.path.basename(video))[0]
    base = urllib.parse.quote(base)
    urls = [
        f"https://api.srrdb.com/v1/search/r:{base}",
        f"https://api.srrdb.com/v1/imdb/{base}"
    ]

    async with httpx.AsyncClient() as client:
        try:
            # First request to check for scene release
            response = await client.get(urls[0], timeout=httpx.Timeout(30.0))
            response.raise_for_status()
            data = response.json()
            if int(data.get('resultsCount', 0)) > 0:
                video = f"{data['results'][0]['release']}.mkv"
                scene = True
                console.print(f"[green]SRRDB: Matched to {data['results'][0]['release']}")

                # Second request to fetch IMDb data if not provided
                if imdb is None:
                    r = await client.get(urls[1])
                    r.raise_for_status()
                    imdb_data = r.json()
                    if imdb_data.get('releases', []):
                        imdb = imdb_data['releases'][0].get('imdb', imdb)
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            console.print("[yellow]SRRDB: No match found, or request has timed out")
            log.debug(f"Exception occurred: {e}")
    
    return video, scene, imdb