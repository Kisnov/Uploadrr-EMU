import asyncio
import anitopy
import re
import requests
import time
import tmdbsimple as tmdb
from datetime import datetime
from imdb import Cinemagoer
from difflib import SequenceMatcher
from src.args import Args
from guessit import guessit
from rich.prompt import Prompt
from requests.exceptions import HTTPError
from functools import lru_cache
from src.console import console, log


class TMDbHandler:
    def __init__(self, config=None):
        self.config = config
        self.search = tmdb.Search()
        self.ia = Cinemagoer()
        self.last_request_time = 0
        self.min_request_interval = 0.25

    @lru_cache(maxsize=1000)
    def _cached_movie_info(self, tmdb_id):
        return tmdb.Movies(tmdb_id).info()

    async def rate_limit(self):
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.min_request_interval:
            await asyncio.sleep(self.min_request_interval - time_since_last)
        self.last_request_time = time.time()

    async def get_tmdb_from_imdb(self, meta, filename):
        if meta.get('tmdb_manual') is not None:
            meta['tmdb'] = meta['tmdb_manual']
            return meta
        imdb_id = meta['imdb']
        if str(imdb_id)[:2].lower() != "tt":
            imdb_id = f"tt{imdb_id}"
        find = tmdb.Find(id=imdb_id)
        info = find.info(external_source="imdb_id")
        if len(info['movie_results']) >= 1:
            meta['category'] = "MOVIE"
            meta['tmdb'] = info['movie_results'][0]['id']
        elif len(info['tv_results']) >= 1:
            meta['category'] = "TV"
            meta['tmdb'] = info['tv_results'][0]['id']
        else:
            imdb_handler = IMDbHandler()
            imdb_info = await imdb_handler.get_imdb_info(imdb_id.replace('tt', ''), meta)
            title = imdb_info.get("title") or filename
            year = imdb_info.get('year') or meta['search_year']
            console.print(f"[yellow]TMDb was unable to find anything with that IMDb, searching TMDb for {title}")
            meta = await self.get_tmdb_id(title, year, meta, meta['category'], imdb_info.get('original title', imdb_info.get('localized title', meta['uuid'])))
            if meta.get('tmdb') in ('None', '', None, 0, '0'):
                if meta.get('mode', 'discord') == 'cli':
                    console.print('[yellow]Unable to find a matching TMDb entry')
                    tmdb_id = console.input("Please enter tmdb id: ")  
                    parser = Args(config=self.config)
                    meta['category'], meta['tmdb'] = parser.parse_tmdb_id(id=tmdb_id, category=meta.get('category'))
        await asyncio.sleep(2)
        return meta

    async def get_tmdb_id(self, filename, search_year, meta, category, untouched_filename="", attempted=0):
        log.debug(f"Searching TMDb: filename={filename}, year={search_year}, category={category}, attempt={attempted}")
        try:
            await self.rate_limit()
            if category == "MOVIE":
                self.search.movie(query=filename, year=search_year, include_adult=True)
            elif category == "TV":
                self.search.tv(query=filename, first_air_date_year=search_year, include_adult=True)
            
            if meta.get('tmdb_manual') is not None:
                meta['tmdb'] = meta['tmdb_manual']
            elif self.search.results:
                meta['tmdb'] = self.search.results[0]['id']
                meta['category'] = category
            else:
                raise IndexError
        except IndexError:
            try:
                if category == "MOVIE":
                    self.search.movie(query=filename, include_adult=True)
                elif category == "TV":
                    self.search.tv(query=filename, include_adult=True)
                meta['tmdb'] = self.search.results[0]['id']
                meta['category'] = category
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
                        tmdb_id = Prompt.ask("Please enter tmdb id in this format: tv/12345 or movie/12345\n")
                        parser = Args(config=self.config)
                        meta['category'], meta['tmdb'] = parser.parse_tmdb_id(id=tmdb_id, category=meta.get('category'))
                        meta['tmdb_manual'] = meta['tmdb']
        return meta


    async def tmdb_other_meta(self, meta):
        if meta['tmdb'] == "0":
            try:
                title = guessit(meta['path'], {"excludes": ["country", "language"]})['title'].lower().split('aka')[0]
                meta = await self.get_tmdb_id(title, meta['search_year'], meta)
                if meta['tmdb'] == "0":
                    meta = await self.get_tmdb_id(title, "", meta, meta['category'])
            except Exception as e:
                log.error(f"Failed to find TMDb entry: {str(e)}")
                if meta.get('mode', 'discord') == 'cli':
                    console.print("[bold red]Unable to find tmdb entry. Exiting.")
                    exit()
                else:
                    console.print("[bold red]Unable to find tmdb entry")
                    return meta

        max_retries = 3
        for attempt in range(max_retries):
            try:
                await self.rate_limit()
                
                if meta['category'] == "MOVIE":
                    movie = tmdb.Movies(meta['tmdb'])
                    response, external, videos, images, credits, keywords = await self._fetch_all_movie_data(movie)
                    
                    # Batch update for basic movie info
                    release_date = response.get('release_date')
                    full_date = ""
                    year = meta['search_year']
                    if release_date:
                        try:
                            date_obj = datetime.strptime(release_date, '%Y-%m-%d')
                            year = date_obj.year
                            full_date = date_obj.strftime('%Y-%m-%d')
                        except Exception:
                            console.print('[yellow]TMDB date parsing failed, using year from filename')
                    else:
                        console.print('[yellow]TMDB does not have a release date, using year from filename')

                    meta.update({
                        'title': response['title'],
                        'year': year,
                        'full_date': full_date,
                        'imdb_id': self._normalize_imdb_id(external.get('imdb_id', meta.get('imdb', '0'))),
                        'tvdb_id': external.get('tvdb_id', meta.get('tvdb_id', '0')) or '0',
                        'adult': response['adult'],
                        'overview': response['overview'],
                        'tmdb_type': 'Movie',
                        'runtime': response.get('episode_run_time', 60)
                    })

                    # Trailer
                    meta['youtube'] = next((v['key'] for v in videos.get('results', []) 
                                        if v.get('site') == 'YouTube' and v.get('type') == 'Trailer'), None)
                    if not meta['youtube']:
                        log.debug(f"No YouTube trailer found for TMDb ID {meta['tmdb']}")

                    # Additional metadata
                    aka, original_language = await IMDbHandler.get_imdb_aka(self, meta['imdb_id'])
                    meta.update({
                        'aka': aka,
                        'original_language': original_language or response['original_language'],
                        'original_title': response.get('original_title', meta['title']),
                        'keywords': ', '.join(kw['name'].replace(',', ' ') for kw in keywords.get('keywords', [])),
                        'genres': ', '.join(g['name'].replace(',', ' ') for g in response.get('genres', [])),
                        'tmdb_directors': [c.get('original_name', c.get('name')) 
                                        for c in credits.get('cast', []) 
                                        if c.get('known_for_department') == 'Directing']
                    })
                    
                    if not meta.get('anime', False):
                        meta['mal_id'], meta['aka'], meta['anime'] = self.get_anime(response, meta)
                    
                    # Poster, Logo, and Backdrop
                    poster_path = response.get('poster_path', "")
                    meta['poster'] = f"https://image.tmdb.org/t/p/original{poster_path}" if poster_path else ""
                    
                    logos = images.get('logos', [])
                    first_logo = next((logo for logo in logos if logo.get('iso_639_1') == 'en'), None) or (logos[0] if logos else None)
                    meta['logo'] = f"https://image.tmdb.org/t/p/original{first_logo['file_path']}" if first_logo else None

                    backdrops = images.get('backdrops', [])
                    first_backdrop = next((bd for bd in backdrops if bd.get('iso_639_1') == 'en'), None) or (backdrops[0] if backdrops else None)
                    meta['backdrop'] = f"https://image.tmdb.org/t/p/original{first_backdrop['file_path']}" if first_backdrop else None

                elif meta['category'] == "TV":
                    tv = tmdb.TV(meta['tmdb'])
                    response, external, videos, images, credits, keywords = await self._fetch_all_tv_data(tv)
                    
                    # Batch update for basic TV info
                    first_air_date = response.get('first_air_date')
                    full_date = ""
                    year = meta['search_year']
                    if first_air_date:
                        try:
                            date_obj = datetime.strptime(first_air_date, '%Y-%m-%d')
                            year = date_obj.year
                            full_date = date_obj.strftime('%Y-%m-%d')
                        except Exception:
                            console.print('[yellow]TMDB date parsing failed, using year from filename')
                    else:
                        console.print('[yellow]TMDB does not have a release date, using year from filename')

                    runtime = response.get('episode_run_time', [60])
                    meta.update({
                        'title': response['name'],
                        'year': year,
                        'full_date': full_date,
                        'imdb_id': self._normalize_imdb_id(external.get('imdb_id', meta.get('imdb', '0'))),
                        'tvdb_id': external.get('tvdb_id', meta.get('tvdb_id', '0')) or '0',
                        'adult': response['adult'],
                        'overview': response['overview'],
                        'tmdb_type': response.get('type', 'Scripted'),
                        'runtime': runtime[0] if runtime else 60
                    })

                    # Trailer
                    meta['youtube'] = next((v['key'] for v in videos.get('results', []) 
                                        if v.get('site') == 'YouTube' and v.get('type') == 'Trailer'), None)

                    # Additional metadata
                    aka, original_language = await IMDbHandler.get_imdb_aka(self, meta['imdb_id'])
                    meta.update({
                        'aka': aka,
                        'original_language': original_language or response['original_language'],
                        'original_title': response.get('original_name', meta['title']),
                        'keywords': ', '.join(kw['name'].replace(',', ' ') for kw in keywords.get('results', [])),
                        'genres': ', '.join(g['name'].replace(',', ' ') for g in response.get('genres', [])),
                        'tmdb_directors': [c.get('original_name', c.get('name')) 
                                        for c in credits.get('cast', []) 
                                        if c.get('known_for_department') == 'Directing']
                    })
                    
                    meta['mal_id'], meta['aka'], meta['anime'] = self.get_anime(response, meta)
                    
                    # Poster, Logo, and Backdrop
                    poster_path = response.get('poster_path', '')
                    meta['poster'] = f"https://image.tmdb.org/t/p/original{poster_path}" if poster_path else ""
                    
                    logos = images.get('logos', [])
                    first_logo = next((logo for logo in logos if logo.get('iso_639_1') == 'en'), None) or (logos[0] if logos else None)
                    meta['logo'] = f"https://image.tmdb.org/t/p/original{first_logo['file_path']}" if first_logo else None

                    backdrops = images.get('backdrops', [])
                    first_backdrop = next((bd for bd in backdrops if bd.get('iso_639_1') == 'en'), None) or (backdrops[0] if backdrops else None)
                    meta['backdrop'] = f"https://image.tmdb.org/t/p/original{first_backdrop['file_path']}" if first_backdrop else None

                # Post-processing
                difference = SequenceMatcher(None, meta['title'].lower(), meta['aka'][5:].lower()).ratio()
                if difference >= 0.9 or meta['aka'][5:].strip() == "" or meta['aka'][5:].strip().lower() in meta['title'].lower():
                    meta['aka'] = ""
                if f"({meta['year']})" in meta['aka']:
                    meta['aka'] = meta['aka'].replace(f"({meta['year']})", "").strip()
                
                break  # Success, exit retry loop

            except HTTPError as e:
                log.error(f"HTTP Error on attempt {attempt + 1}: {str(e)}")
                if attempt == max_retries - 1:
                    console.print(f"[red]Failed after {max_retries} attempts: {str(e)}")
                    return meta
                elif e.response.status_code == 429:  # Too Many Requests
                    await asyncio.sleep(2 ** attempt)
                elif e.response.status_code == 404:
                    console.print("[red]The TMDb ID you entered could not be found.")
                    tmdb_id = Prompt.ask("Please enter tmdb id in this format: tv/12345 or movie/12345\n")
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

    async def _fetch_all_movie_data(self, movie):
        await self.rate_limit()
        response = movie.info(adult=True)
        external = movie.external_ids()
        videos = movie.videos()
        images = movie.images()
        credits = movie.credits()
        keywords = movie.keywords()
        return response, external, videos, images, credits, keywords

    async def _fetch_all_tv_data(self, tv):
        await self.rate_limit()
        response = tv.info(adult=True)
        external = tv.external_ids()
        videos = tv.videos()
        images = tv.images()
        credits = tv.credits()
        keywords = tv.keywords()
        return response, external, videos, images, credits, keywords

    def _normalize_imdb_id(self, imdb_id):
        return str(int(imdb_id.replace('tt', ''))).zfill(7) if imdb_id and imdb_id != "0" else '0'

    def get_keywords(self, tmdb_info):
        if tmdb_info:
            tmdb_keywords = tmdb_info.keywords()
            keywords = [keyword['name'].replace(',', ' ') for keyword in tmdb_keywords.get('keywords', []) or tmdb_keywords.get('results', [])]
            return ', '.join(keywords)
        return ''

    def get_genres(self, tmdb_info):
        if tmdb_info:
            genres = [genre['name'].replace(',', ' ') for genre in tmdb_info.get('genres', [])]
            return ', '.join(genres)
        return ''

    def get_directors(self, tmdb_info):
        if tmdb_info:
            tmdb_credits = tmdb_info.credits()
            return [each.get('original_name', each.get('name')) for each in tmdb_credits.get('cast', []) if each.get('known_for_department') == "Directing"]
        return []

    def get_tmdb_imdb_from_mediainfo(self, mediainfo, category, is_disc, tmdbid, imdbid):
        if not is_disc and mediainfo['media']['track'][0].get('extra'):
            extra = mediainfo['media']['track'][0]['extra']
            for each in extra:
                if each.lower().startswith('tmdb'):
                    parser = Args(config=self.config)
                    category, tmdbid = parser.parse_tmdb_id(id=extra[each], category=category)
                if each.lower().startswith('imdb'):
                    try:
                        imdbid = str(int(extra[each].replace('tt', ''))).zfill(7)
                    except Exception:
                        pass
        return category, tmdbid, imdbid

    def daily_to_tmdb_season_episode(self, tmdbid, date):
        show = tmdb.TV(tmdbid)
        seasons = show.info().get('seasons')
        season, episode = '1', '1'
        date = datetime.fromisoformat(str(date))
        for each in seasons:
            air_date = datetime.fromisoformat(each['air_date'])
            if air_date <= date:
                season = str(each['season_number'])
        season_info = tmdb.TV_Seasons(tmdbid, season).info().get('episodes')
        for each in season_info:
            if str(each['air_date']) == str(date):
                episode = str(each['episode_number'])
                break
        else:
            console.print(f"[yellow]Unable to map the date ([bold yellow]{str(date)}[/bold yellow]) to a Season/Episode number")
        return season, episode


    def get_anime(self, response, meta):
        tmdb_name = meta['title']
        if meta.get('aka', "") == "":
            alt_name = ""
        else:
            alt_name = meta['aka']
        anime = False
        animation = False
        for each in response['genres']:
            if each['id'] == 16:
                animation = True
        if response['original_language'] == 'ja' and animation is True:
            romaji, mal_id, eng_title, season_year, episodes = self.get_romaji(tmdb_name, meta.get('mal', None))
            alt_name = f" AKA {romaji}"
            
            anime = True
            # mal = AnimeSearch(romaji)
            # mal_id = mal.results[0].mal_id
        else:
            mal_id = 0
        if meta.get('mal_id', 0) != 0:
            mal_id = meta.get('mal_id')
        if meta.get('mal') not in ('0', 0, None):
            mal_id = meta.get('mal', 0)
        return mal_id, alt_name, anime

    def get_romaji(self, tmdb_name, mal):
        if mal is None:
            mal = 0
            tmdb_name = tmdb_name.replace('-', "").replace("The Movie", "")
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
            # Define our query variables and values that will be used in the query request
            variables = {
                'search': tmdb_name
            }
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
            # Define our query variables and values that will be used in the query request
            variables = {
                'search': mal
            }

        # Make the HTTP Api request
        url = 'https://graphql.anilist.co'
        try:
            response = requests.post(url, json={'query': query, 'variables': variables})
            json = response.json()
            media = json['data']['Page']['media']
        except:
            console.print('[red]Failed to get anime specific info from anilist. Continuing without it...')
            media = []
        if media not in (None, []):
            result = {'title' : {}}
            difference = 0
            for anime in media:
                search_name = re.sub(r"[^0-9a-zA-Z\[\]]+", "", tmdb_name.lower().replace(' ', ''))
                for title in anime['title'].values():
                    if title is not None:
                        title = re.sub(u'[\u3000-\u303f\u3040-\u309f\u30a0-\u30ff\uff00-\uff9f\u4e00-\u9faf\u3400-\u4dbf]+ (?=[A-Za-z ]+–)', "", title.lower().replace(' ', ''), re.U)
                        diff = SequenceMatcher(None, title, search_name).ratio()
                        if diff >= difference:
                            result = anime
                            difference = diff

            romaji = result['title'].get('romaji', result['title'].get('english', ""))
            mal_id = result.get('idMal', 0)
            eng_title = result['title'].get('english', result['title'].get('romaji', ""))
            season_year = result.get('season_year', "")
            episodes = result.get('episodes', 0)
        else:
            romaji = eng_title = season_year  = ""
            episodes = mal_id = 0
        if mal_id in [None, 0]:
            mal_id = mal
        if not episodes:
            episodes = 0
        return romaji, mal_id, eng_title, season_year, episodes


class IMDbHandler:
    def __init__(self):
        self.ia = Cinemagoer()

    async def get_imdb_info(self, imdb_id, meta):
        imdb_info = {}
        if int(str(imdb_id).replace('tt', '')) != 0:
            info = self.ia.get_movie(imdb_id)
            self.ia.update(info, ['technical'])
            imdb_info['title'] = info.get('title')
            imdb_info['year'] = info.get('year')
            # ... (rest of the IMDb info logic)
        else:
            imdb_info = {
                'title': meta['title'],
                'year': meta['year'],
                # ... (rest of the fallback logic)
            }
        return imdb_info

    async def search_imdb(self, filename, search_year):
        imdb_id = '0'
        search = self.ia.search_movie(filename)
        for movie in search:
            if filename in movie.get('title', '') and movie.get('year') == search_year:
                imdb_id = str(movie.movieID).replace('tt', '')
        return imdb_id

    async def imdb_other_meta(self, meta):
        meta['imdb_info'] = await self.get_imdb_info(meta['imdb_id'], meta)
        meta['title'] = meta['imdb_info']['title']
        # ... (rest of the IMDb meta logic)
        return meta

    async def get_imdb_aka(self, imdb_id):
        if imdb_id == "0":
            return "", None
        result = self.ia.get_movie(imdb_id.replace('tt', ''))
        original_language = result.get('language codes')
        aka = result.get('original title', result.get('localized title', "")).replace(' - IMDb', '').replace('\u00ae', '')
        if aka:
            aka = f" AKA {aka}"
        return aka, original_language


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