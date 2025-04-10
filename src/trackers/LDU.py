# -*- coding: utf-8 -*-
# import discord
import asyncio
import requests
import re
import langcodes
import httpx
import platform
from rich.pretty import Pretty

from src.trackers.COMMON import COMMON
from src.console import console, log


class LDU():

    def __init__(self, config):
        self.config = config
        self.type = 'unit3d'
        self.tracker = 'LDU'
        self.source_flag = 'LDU'
        self.domain = 'https://theldu.to'
        self.upload_url = f'{self.domain}/api/torrents/upload'
        self.search_url = f'{self.domain}/api/torrents/filter'
        pid = config['TRACKERS'][self.tracker].get('PID', 'PASSKEY_HERE')
        self.announce_url = f"{self.domain}/announce/{pid}"
        self.banned_groups = ['']
        self.allowed_imghosts = []
        pass
    
    async def get_cat_id(self, meta):
        category_name = meta['category']
        anime = meta.get('anime', False)
        genres = list(map(str.strip, meta.get('genres', '').lower().split(',')))
        keywords = list(map(str.strip, meta.get('keywords', '').lower().split(',')))
        edition = meta.get('edition', '')
        adult = meta.get('adult', False)
        is_silent = meta.get('is_silent', False)
        original_language = meta.get('original_language')
        hc = meta.get('hardcoded_subs', False)

        tag = self.get_language_tag(meta) or ''
        tags = [t[1:] for t in tag.split(']') if t]

        category_id = {
            'MOVIE': '1',
            'TV': '2',
            'Anime': '8',
            'FANRES': '12',
            'SPORTS': '35',
            'MUSIC': '3',
        }.get(category_name, '0')

        if category_name == "SPORTS":
            return '35'
        if category_name == "MUSIC":
            return '3'

        if adult and (anime or 'hentai' in keywords or 'animation' in keywords):
            return '10'  # Adult anime/hentai
        if adult:
            return '6'   # Adult content

        if category_name == 'MOVIE':
            if (meta['is_disc'] != 'DVD' and '???' in tags and original_language != "en") or hc:
                return '27'
            if 'ENG' not in ''.join(tags):
                return '22'
            if '3D' in edition:
                return '21'
            if any(x in edition.upper() for x in ['FANRES', 'FANEDIT', 'FANFIX', 'RESTORATION']):
                return '12'
            if anime or 'anime' in keywords:
                return '8'
            if 'silent film' in keywords or is_silent:
                return '18'
            if 'holiday' in keywords:
                return '24'
            if 'musical' in keywords:
                return '25'
            if 'documentary' in genres:
                return '17'
            if 'stand-up' in keywords or 'stand-up comedy' in keywords:
                return '20'
            if 'short film' in keywords:
                return '19'
            return '1'  # Default for MOVIE

        if category_name == 'TV':
            if (meta['is_disc'] != 'DVD' and '???' in tags and original_language != "en") or hc:
                return '31'
            if 'ENG' not in ''.join(tags):
                return '29'
            if anime or 'anime' in keywords:
                return '9'
            return '2'  # Default for TV

        return category_id 


    async def get_type_id(self, type, edition):
        type_id = {
            'DISC': '1', 
            'REMUX': '2',
            'WEBDL': '4', 
            'WEBRIP': '5', 
            'HDTV': '6',
            'ENCODE': '3',
            'UPSCALE': '27',
            'AAC': '10',
            'FLAC': '7',
            'MP3': '11',
            'ALAC': '8',
            'AC3': '9',
            'WAVE': '14',
            'OGG': '14',
            'MPEG4': '14',            
            }.get(type, '0')
        if type == 'ENCODE':
            if 'upscale' in edition.lower() or ' ai' in edition.lower():
                type_id = '27'   
        return type_id

    async def get_res_id(self, resolution):
        resolution_id = {
            '8640p':'10', 
            '4320p': '1', 
            '2160p': '2', 
            '1440p' : '3',
            '1080p': '3',
            '1080i':'4', 
            '720p': '5',  
            '576p': '6', 
            '576i': '7',
            '480p': '8', 
            '480i': '9'
            }.get(resolution, '10')
        return resolution_id

    ###############################################################
    ######   STOP HERE UNLESS EXTRA MODIFICATION IS NEEDED   ######
    ###############################################################

    async def upload(self, meta):
        is_music = meta.get('is_music', False)
        common = COMMON(config=self.config)
        await common.edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)
        cat_id = await self.get_cat_id(meta)
        type_id = await self.get_type_id(meta['type'],meta['edition'])
        resolution_id = await self.get_res_id(meta['resolution'])
        await common.unit3d_edit_desc(meta, self.tracker)
        manual_name = meta.get('manual_name')
        ldu_name = meta['name'] if is_music else await self.edit_name(meta)
        region_id = await common.unit3d_region_ids(meta.get('region'))
        distributor_id = await common.unit3d_distributor_ids(meta.get('distributor'))
        if meta['anon'] != 0 or self.config['TRACKERS'][self.tracker].get('anon', "False"):
            anon = 1
        else:
            anon = 0

        if meta['bdinfo'] is not None:
            mi_dump = None
            bd_dump = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/BD_SUMMARY_00.txt", 'r', encoding='utf-8').read()
        else:
            mi_dump = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt", 'r', encoding='utf-8').read()
            bd_dump = None

        desc = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}]DESCRIPTION.txt", 'r', encoding='utf-8').read()
        if cat_id == '10':
            poster = meta.get('poster')
            backdrop = meta.get('backdrop')
            url_string = ""
            if poster:
                url_string += f"[url=torrent-cover={poster} ] [/url] \n"
            if backdrop:
                url_string += f"[url=torrent-banner={backdrop} ] [/url]"
            desc += url_string        
        nfo_file = meta.get('nfo_file', None)

        data = {
            'name': manual_name or ldu_name,
            'description': desc,
            'mediainfo': mi_dump,
            'category_id': cat_id,
            'type_id': type_id,
            'tmdb': meta.get('tmdb', 0),
            'imdb': meta.get('imdb_id', 0).replace('tt', '') if not is_music else 0,
            'tvdb': meta.get('tvdb_id', 0),
            'mal': meta.get('mal_id', 0),
            'igdb': 0,
            'anonymous': anon,
            'stream': int(meta.get('stream', False)),
            'sd': meta['sd'],
            'keywords': meta['keywords'],
            'personal_release': int(meta.get('personalrelease', False)),
            'internal': 0,
            'featured': 0,
            'free': 0,
            'doubleup': 0,
            'sticky': 0,
        }

        if not is_music:
            data.update({
                'resolution_id': resolution_id,
                'bdinfo': bd_dump,
            })
        # Internal
        if self.config['TRACKERS'][self.tracker].get('internal', False):
            if meta['tag'] != "" and (meta['tag'][1:] in self.config['TRACKERS'][self.tracker].get('internal_groups', [])):
                data['internal'] = 1
                
        if region_id != 0:
            data['region_id'] = region_id
        if distributor_id != 0:
            data['distributor_id'] = distributor_id
        if meta.get('category') == "TV" and not cat_id == '10':
            data['season_number'] = int(meta.get('season_int', 0))
            data['episode_number'] = int(meta.get('episode_int', 0))
        headers = {
            'User-Agent': f'Uploadrr / v1.0 ({platform.system()} {platform.release()})'
        }
        params = {
            'api_token' : self.config['TRACKERS'][self.tracker]['api_key'].strip()
        }

        if meta.get('debug', False):
            log.debug("[magenta]DATA 2 SEND[/magenta]:")
            console.print(Pretty(data))
            return False
        async with httpx.AsyncClient() as client:
            files = {}
            torrent_content = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}]{meta['clean_name']}.torrent", 'rb').read()
            files['torrent'] = (f"{meta['clean_name']}.torrent", torrent_content, 'application/x-bittorrent')

            if nfo_file:
                nfo_content= open(nfo_file, 'rb').read()
                files['nfo'] = ('nfo.nfo', nfo_content, 'application/octet-stream')

            try:
                response = await client.post(
                    self.upload_url, data=data, files=files if files else None,
                    headers=headers, params=params
                )
                if 200 <= response.status_code < 300:
                    response_json = response.json()
                    success = response_json.get('success', False)
                    if success:
                        console.print("[bold green]Torrent uploaded successfully!")
                    else:
                        message = response_json.get('message', 'No message provided')
                        console.print(f"[red]Upload failed: {message}[/red]")
                        if 'data' in response_json:
                            console.print(f"[cyan]Error details:[/cyan] {response_json['data']}")
                    return success
                else:
                    log.error(f"[red]Encountered HTTP Error: {response.status_code}[/red]")
                    console.print(f"[blue]Server Response[/blue]: {response.text}")
                    return False
            except Exception as e:
                log.error(f"[red]Encountered Error: {e}[/red]")
                return False


    def get_language_tag(self, meta):
        soundmix = meta.get('imdb_info', {}).get('soundmix', [])
        is_silent = 'silent' in soundmix if soundmix else False 

        def map_language(language):
            try:
                lang_code = langcodes.find(language).to_alpha3()
                return lang_code.upper()
            except langcodes.LanguageTagError:
                return '???'

        audio_lang = []
        sub_lang = []
        if meta.get("is_disc") == "BDMV":
            if 'discs' in meta and meta['discs']:
                bdinfo = meta['discs'][0].get('bdinfo', {})
                audio_tracks = bdinfo.get('audio', [])
                subtitle_tracks = bdinfo.get('subtitles', [])
                
                for track in audio_tracks:
                    language = track.get('language')
                    if language and 'comment' not in language.lower() and 'review' not in language.lower():
                        audio_lang.append(map_language(language))

                audio_lang = list(dict.fromkeys(audio_lang))  # Remove dupes + keep order
                
                if not audio_lang:
                    audio_lang.append('???')
                
                if is_silent:
                    lang_tag = "[Silent]"
                else:    
                    lang_tag = f"[{' '.join(audio_lang)}]"
                
                for lang in subtitle_tracks:
                    sub_lang.append(map_language(lang))
                
                if not sub_lang:
                    sub_lang_tag = "[No Subs]"
                else:
                    sub_lang = list(dict.fromkeys(sub_lang))  # Remove dupes + keep order
                    if 'ENG' in sub_lang:
                        if len(sub_lang) > 1:
                            sub_lang_tag = "[Subs ENG+]"
                        else:
                            sub_lang_tag = "[Subs ENG]"
                    else:
                        if sub_lang[0] == '???':
                            sub_lang_tag = "[Subs ???]"
                        else:
                            if len(sub_lang) > 1:
                                sub_lang_tag = f"[Subs {sub_lang[0]}+]"
                            else:
                                sub_lang_tag = f"[Subs {sub_lang[0]}]"
        elif 'mediainfo' in meta:
            for x in meta["mediainfo"]["media"]["track"]:
                if x["@type"] == "Audio":
                    commentary_found = 'Title' in x and ('comment' in x['Title'].lower() or 'review' in x['Title'].lower())
                    if not commentary_found and 'Language_String3' in x:
                        audio_lang.append(x.get('Language_String3'))
            
            audio_lang = list(dict.fromkeys(audio_lang))  # Remove dupes + keep order
            if not audio_lang:
                audio_lang.append('???')
            if is_silent:
                lang_tag = "[Silent]"
            else:        
                lang_tag = f"[{' '.join(lang.upper() for lang in audio_lang)}]"
            
            sub_lang = [x.get('Language_String3') for x in meta["mediainfo"]["media"]["track"] if x["@type"] == "Text"]
            if not sub_lang:
                sub_lang_tag = "[No Subs]"
            else:
                sub_lang = list(dict.fromkeys(sub_lang))  # Remove dupes + keep order
                if 'eng' in sub_lang:
                    if len(sub_lang) > 1:
                        sub_lang_tag = "[Subs ENG+]"
                    else:
                        sub_lang_tag = "[Subs ENG]"
                else:
                    if sub_lang[0] is None:
                        sub_lang_tag = "[Subs ???]"
                    else:
                        if len(sub_lang) > 1:
                            sub_lang_tag = f"[Subs {sub_lang[0].upper()}+]"
                        else:
                            sub_lang_tag = f"[Subs {sub_lang[0].upper()}]"
        else:
            audio_lang.append('???')
            if is_silent:
                lang_tag = "[Silent]"
            else:    
                lang_tag = f"[{' '.join(audio_lang)}]"
            sub_lang_tag = "[No Subs]"

        return lang_tag + " " + sub_lang_tag

    async def edit_name(self, meta) -> str:
        name = meta['name']
        bracket_fields = ["cut", "ratio", "edition", "repack", "resolution", "source"]       
        tag = meta.get("tag", '-NOGRP') if not meta.get("no_tag", False) else ""
        year = str(meta.get('year', ''))

        found = None
        for field in bracket_fields:
            value = meta.get(field, "").strip()
            if value:
                found = value
                break

        if found and re.search(rf"\b{found}\b", name):
            parts = re.split(rf"\b{found}\b", name, 1)
            name = f"{parts[0].strip()} [{found} {parts[1].strip()}"
            name = name.replace(tag, f"{tag}]")        
        name = name.replace(year, f'({year})')

        lang_tag = self.get_language_tag(meta)
        if lang_tag:
            name += f" {lang_tag}"

        return " ".join(name.split())

    async def search_existing(self, meta):
        dupes = {}
        console.print("[yellow]Searching for existing torrents on site...")
        params = {
            'api_token': self.config['TRACKERS'][self.tracker]['api_key'].strip(),
            'categories[]': await self.get_cat_id(meta),
            'types[]': await self.get_type_id(meta['type'], meta['edition']),
            'name': meta.get('album', '') if meta.get('is_music', False) else ""
        }

        if not meta.get('is_music', False):
            params.update({
                'tmdbId': meta['tmdb'],
                'resolutions[]': await self.get_res_id(meta['resolution'])
            })

            if meta.get('edition'):
                params['name'] = f"{meta['edition']}".strip()

        try:
            response = requests.get(url=self.search_url, params=params).json()
            for each in response.get('data', []):
                result = each['attributes']['name']
                size = each['attributes']['size']

                # Extract the main part of the name using regex
                match = re.split(r'(-\w*\])', result)
                if len(match) > 1:
                    result = match[0] + match[1]
                
                # Filter results and populate dupes
                if "🔥Eternal" not in result:
                    dupes[result] = size
        except requests.exceptions.RequestException as req_err:
            log.error(f'[bold red]Request error while searching for torrents: {req_err}')
        except Exception as e:
            log.error(f'[bold red]Unexpected error during search: {e}')
            await asyncio.sleep(5)

        log.debug(f'[blue]Dupe List[/blue]:\n {dupes}')
        return dupes
