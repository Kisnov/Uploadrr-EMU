# -*- coding: utf-8 -*-
import asyncio
import requests
from typing import Dict
from src.trackers.COMMON import COMMON
from src.console import console, log
from rich.pretty import Pretty
import platform

class UTP():

    def __init__(self, config):
        self.config = config
        self.type = 'unit3d'
        self.tracker = 'UTP'
        self.source_flag = 'UTOPIA'
        self.domain = 'https://utp.to'
        self.search_url = f'{self.domain}/api/torrents/filter'
        self.upload_url = f'{self.domain}/api/torrents/upload'
        pid = config['TRACKERS'][self.tracker].get('PID', 'PASSKEY_HERE')
        self.announce_url = f"{self.domain}/announce/{pid}"      
        self.banned_groups = [""]
        pass

    async def upload(self, meta):
        common = COMMON(config=self.config)
        await common.edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)
        await common.unit3d_edit_desc(meta, self.tracker, comparison=True)
        cat_id = await self.get_cat_id(meta['category'], meta.get('edition', ''))
        type_id = await self.get_type_id(meta['type'])
        resolution_id = await self.get_res_id(meta['resolution'])
        region_id = await common.unit3d_region_ids(meta.get('region'))
        distributor_id = await common.unit3d_distributor_ids(meta.get('distributor'))
        if meta['anon'] != 0 or self.config['TRACKERS'][self.tracker].get('anon', False):
            anon = 1
        else:
            anon = 0

        if meta['bdinfo'] != None:
            mi_dump = None
            bd_dump = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/BD_SUMMARY_00.txt", 'r', encoding='utf-8').read()
        else:
            mi_dump = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt", 'r', encoding='utf-8').read()
            bd_dump = None
        desc = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}DESCRIPTION.txt", 'r', encoding='utf-8').read()
        open_torrent = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}]{meta['clean_name']}.torrent", 'rb')
        nfo_file = meta.get('nfo_file', None)
        files = {'torrent': ("placeholder.torrent", open_torrent, "application/x-bittorrent")}
        if nfo_file:
            open_nfo = open(nfo_file, 'rb') 
            files['nfo'] = open_nfo
        manual_name = meta.get('manual_name')
        data = {
            'name' : manual_name or await self.edit_name(meta),
            'description' : desc,
            'mediainfo' : mi_dump,
            'bdinfo' : bd_dump,
            'category_id' : cat_id,
            'media_typeid' : type_id,
            'resolution_id' : resolution_id,
            'tmdb' : meta['tmdb'],
            'imdb' : meta['imdb_id'].replace('tt', ''),
            'tvdb' : meta['tvdb_id'],
            'mal' : meta['mal_id'],
            'igdb' : 0,
            'anonymous' : anon,
            'stream' : meta['stream'],
            'sd' : meta['sd'],
            'keywords' : meta['keywords'],
            'personal_release' : int(meta.get('personalrelease', False)),
            'internal' : 0,
            'featured' : 0,
            'free' : 0,
            'doubleup' : 0,
            'sticky' : 0
        }
        # Internal
        if self.config['TRACKERS'][self.tracker].get('internal', False):
            if meta['tag'] != "" and (meta['tag'][1:] in self.config['TRACKERS'][self.tracker].get('internal_groups', [])):
                data['internal'] = 1

        if region_id != 0:
            data['region_id'] = region_id
        if distributor_id != 0:
            data['distributor_id'] = distributor_id
        if meta.get('category') == "TV":
            data['season_number'] = int(meta.get('season_int', '0'))
            data['episode_number'] = int(meta.get('episode_int', '0'))
        headers = {
            'User-Agent': f'Uploadrr ({platform.system()} {platform.release()})'
        }
        params = {
            'api_token': self.config['TRACKERS'][self.tracker]['api_key'].strip()
        }

        if meta['debug']:
            log.debug("[blue]DATA 2 SEND[/blue]:")
            console.print(Pretty(data))

        else:
            success = 'Unknown'
            try:
                response = requests.post(url=self.upload_url, files=files, data=data, headers=headers, params=params)
                
                if response.status_code >= 200 and response.status_code < 300:
                    response_json = response.json()
                    success = response_json.get('success', False)
                    data = response_json.get('data', {})

                    if not success:
                        message = response_json.get('message', 'No message provided')
                        console.print(f"[red]Upload failed: {message}[/red]")
                        if data:
                            console.print(f"[cyan]Error details:[/cyan] {data}")

                else:
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(response.text, 'html.parser')
                        error_heading = soup.find(class_='error__heading')
                        error_body = soup.find(class_='error__body')
                        
                        if error_heading and error_body:
                            console.print(f"[red]{error_heading.text.strip()}[/red]")
                            console.print(f"[b][yellow]{error_body.text.strip()}[/yellow][/b]")
                        else:
                            console.print(f"[red]Encountered HTTP Error: {response.status_code}[/red]")
                            console.print(f"[blue]Server Response[/blue]: {response.text}")
                    except Exception as parse_error:
                        console.print(f"[red]Failed to parse error response: {parse_error}[/red]")
                        console.print(f"[blue]Server Response[/blue]: {response.text}")
                    
                    success = False
                    data = {}

            except requests.exceptions.RequestException as e:
                console.print(f"[red]Encountered Error: {e}[/red]")
                data = {}

            if success == 'Unknown':
                console.print("[bold yellow]Status of upload is unknown, please go check..")

            elif success:
                console.print("[bold green]Torrent uploaded successfully!")
            else:
                console.print("[bold red]Torrent upload failed.")
            
            try:
                open_torrent.close()
            except Exception as e:
                console.print(f"[red]Failed to close torrent file: {e}[/red]")

            return success


    async def get_cat_id(self, category_name, edition):
        category_id = {
            'MOVIE': '1',
            'TV': '2',
            'FANRES': '3'
            }.get(category_name, '0')
        if category_name == 'MOVIE' and 'FANRES' in edition:
            category_id = '3'
        return category_id

    async def get_type_id(self, type):
        media_typeid = {
            'DISC': '1',
            'REMUX': '2',
            'WEBDL': '4',
            'WEBRIP': '5',
            'HDTV': '6',
            'ENCODE': '3'
            }.get(type, '0')
        return media_typeid

    async def get_res_id(self, resolution):
        resolution_id = {
            '4320p': '1',
            '2160p': '2',
            '1080p': '3',
            '1080i': '4'
            }.get(resolution, '1')
        return resolution_id

    async def edit_name(self, meta: Dict):
        name = meta['name']
        category = meta.get('category', '')
        media_type = meta.get('type', '')
        source = meta.get('source', '').upper()
        is_disc = meta.get('is_disc', '')
        title = meta.get('title', '')
        alt_title = meta.get('aka', '') if not meta.get('no_aka', False) else ''
        year = meta.get('year', '') if not meta.get('no_year', False) else ''
        resolution = meta.get('resolution', '')
        if resolution == 'OTHER':
            resolution = ''
        audio = meta.get('audio', '')
        season = meta.get('season', '') if not meta.get('no_season', False) else ''
        episode = meta.get('episode', '')
        episode_title = meta.get('episode_title', '')
        tag = meta.get('tag', '')
        video_codec = meta.get('video_codec', '') if is_disc in ('BDMV', 'HDDVD') or media_type in ('HDTV',) else ''

        if not tag and not name.upper().endswith(('-NOGRP', '-NOGROUP')):
            name = name + '-NOGRP'

        # Source renaming
        if media_type == 'REMUX':
            if source == 'BLURAY':
                name = name.replace('REMUX BluRay', 'BDRemux') if 'REMUX BluRay' in name else name.replace('REMUX', 'BDRemux')
            elif source in ('PAL DVD', 'NTSC DVD', 'DVD'):
                name = name.replace('REMUX PAL DVD', 'DVDRemux').replace('REMUX NTSC DVD', 'DVDRemux').replace('REMUX DVD', 'DVDRemux')
            elif source == 'HDDVD':
                name = name.replace('REMUX HDDVD', 'DVDRemux')
        elif media_type == 'ENCODE':
            name = name.replace(f'{source}', 'BDRip') if source in name else name.replace('ENCODE', 'BDRip')

        # Reorder: insert alt_title after title if needed
        if alt_title and alt_title not in name:
            title_pos = name.find(title) + len(title)
            name = f"{name[:title_pos]} {alt_title}{name[title_pos:]}"

        # Adjust year for TV if needed
        if category == 'TV' and year:
            # Ensure year is after title or season/episode based on type
            if media_type in ('DISC', 'ENCODE', 'HDTV') and year not in name:
                title_pos = name.find(title) + len(title)
                name = f"{name[:title_pos]} {year}{name[title_pos:]}"
            elif media_type in ('REMUX', 'WEBDL', 'WEBRIP') and year not in name:
                episode_pos = name.find(f'{season}{episode}') + len(f'{season}{episode}')
                name = f"{name[:episode_pos]} {year}{name[episode_pos:]}"

        # Add episode_title for TV if missing
        if category == 'TV' and episode_title and media_type in ('REMUX', 'ENCODE', 'WEBDL', 'WEBRIP', 'HDTV'):
            if episode_title not in name:
                episode_pos = name.find(f'{season}{episode}') + len(f'{season}{episode}')
                name = f"{name[:episode_pos]} {episode_title}{name[episode_pos:]}"

        # Remove fields not needed
        if media_type == 'REMUX' and source == 'BLURAY':
            for field in [video_codec, audio]:
                if field in name:
                    name = name.replace(field, '').strip()
        elif media_type == 'ENCODE':
            if audio in name:
                name = name.replace(audio, '').strip()

        name = name.replace(': ', ' - ')
        return ' '.join(name.split())


    async def search_existing(self, meta):
        dupes = {}
        console.print("[yellow]Searching for existing torrents on site...")
        params = {
            'api_token' : self.config['TRACKERS'][self.tracker]['api_key'].strip(),
            'tmdbId' : meta['tmdb'],
            'categories[]' : await self.get_cat_id(meta['category'], meta.get('edition', '')),
            'types[]' : await self.get_media_typeid(meta['type']),
            'resolutions[]' : await self.get_res_id(meta['resolution']),
            'name' : ""
        }
        if meta['category'] == 'TV':
            params['name'] = params['name'] + f" {meta.get('season', '')}{meta.get('episode', '')}"
        if meta.get('edition', "") != "":
            params['name'] = params['name'] + f" {meta['edition']}"
        try:
            response = requests.get(url=self.search_url, params=params)
            response = response.json()
            for each in response['data']:
                result = each['attributes']['name']
                size = each['attributes']['size']
                dupes[result] = size
        except Exception:
            console.print('[bold red]Unable to search for existing torrents on site. Either the site is down or your API key is incorrect')
            await asyncio.sleep(5)

        return dupes
