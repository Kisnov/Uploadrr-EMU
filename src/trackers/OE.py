# -*- coding: utf-8 -*-
import asyncio
import requests
import json
from typing import Dict
import os
import platform
from rich.pretty import Pretty
from src.trackers.COMMON import COMMON
from src.console import console, log

class OE():
    def __init__(self, config):
        self.config = config
        self.type = 'unit3d'
        self.tracker = 'OE'
        self.source_flag = 'OE'
        self.domain = 'https://onlyencodes.cc'
        self.search_url = f'{self.domain}/api/torrents/filter'
        self.upload_url = f'{self.domain}/api/torrents/upload'
        pid = config['TRACKERS'][self.tracker].get('PID', 'PASSKEY_HERE')
        self.announce_url = f"{self.domain}/announce/{pid}"
        self.banned_groups = [
        "0neshot", "3LT0N", "4K4U", "4yEo", "$andra", "[Oj]", "AFG", "AkihitoSubs", "AniHLS", "Anime Time",
        "AnimeRG", "AniURL", "AOC", "AR", "AROMA", "ASW", "aXXo", "BakedFish", "BiTOR", "BRrip",
        "bonkai", "Cleo", "CM8", "C4K", "CrEwSaDe", "core", "d3g", "DDR", "DE3PM", "DeadFish", "DeeJayAhmed",
        "DNL", "ELiTE", "EMBER", "eSc", "EVO", "EZTV", "FaNGDiNG0", "FGT", "fenix", "FUM", "FRDS", "FROZEN",
        "GalaxyTV", "GalaxyRG", "GalaxyRG265", "GERMini", "Grym", "GrymLegacy", "HAiKU", "HD2DVD", "HDTime",
        "Hi10", "HiQVE", "ION10", "iPlanet", "JacobSwaggedUp", "JIVE", "Judas", "KiNGDOM", "LAMA", "Leffe",
        "LiGaS", "LOAD", "LycanHD", "MeGusta", "MezRips", "mHD", "Mr.Deadpool", "mSD", "NemDiggers", "neoHEVC",
        "NeXus", "NhaNc3", "nHD", "nikt0", "nSD", "NOIVTC", "pahe.in", "PlaySD", "playXD", "PRODJi", "ProRes",
        "project-gxs", "PSA", "QaS", "Ranger", "RAPiDCOWS", "RARBG", "Raze", "RCDiVX", "RDN", "Reaktor",
        "REsuRRecTioN", "RMTeam", "ROBOTS", "rubix", "SANTi", "SHUTTERSHIT", "SpaceFish", "SPASM", "SSA", "TBS",
        "Telly", "Tenrai-Sensei", "TERMiNAL", "TGx", "TM", "topaz", "ToVaR", "TSP", "TSPxL", "UnKn0wn",
        "URANiME", "UTR", "VipapkSudios", "ViSION", "WAF", "Wardevil", "x0r", "xRed", "XS", "YakuboEncodes",
        "YIFY", "YTS", "YuiSubs", "ZKBL", "ZmN", "ZMNT"
]
        pass
    
    async def upload(self, meta):
        common = COMMON(config=self.config)
        await common.edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)
        await common.unit3d_edit_desc(meta, self.tracker)
        cat_id = await self.get_cat_id(meta['category'])
        type_id = await self.get_type_id(meta['type'], meta.get('tv_pack', 0), meta.get('video_codec'), meta.get('category', ""))
        resolution_id = await self.get_res_id(meta['resolution'])
        if meta['anon'] != 0 or self.config['TRACKERS'][self.tracker].get('anon', False):
            anon = 1
        else:
            anon = 0
        if meta['bdinfo'] not in (None, ''):
            mi_dump = None
            bd_dump = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/BD_SUMMARY_00.txt", 'r', encoding='utf-8').read()
        else:
            mi_dump = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt", 'r', encoding='utf-8').read()
            bd_dump = None
        desc = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}]DESCRIPTION.txt", 'r', encoding='utf-8').read()
        open_torrent = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[{self.tracker}]{meta['clean_name']}.torrent", 'rb')
        nfo_file = meta.get('nfo_file', None)
        files = {'torrent': open_torrent}
        if nfo_file:
            open_nfo = open(nfo_file, 'rb') 
            files['nfo'] = open_nfo
        manual_name = meta.get('manual_name')
        data = {
            'name' : manual_name or await self.edit_name,
            'description' : desc,
            'mediainfo' : mi_dump,
            'bdinfo' : bd_dump, 
            'category_id' : cat_id,
            'type_id' : type_id,
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
            'sticky' : 0,
        }
        # Internal
        if self.config['TRACKERS'][self.tracker].get('internal', False):
            if meta['tag'] != "" and (meta['tag'][1:] in self.config['TRACKERS'][self.tracker].get('internal_groups', [])):
                data['internal'] = 1
                
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

    def get_language_tag(self, meta: Dict) -> str:
        lang_tag = ""
        has_eng_audio = False
        audio_lang = ""
        if meta.get('is_disc') != "BDMV":
            try:
                with open(f"{meta.get('base_dir')}/tmp/{meta.get('uuid')}/MediaInfo.json", 'r', encoding='utf-8') as f:
                    mi = json.load(f)
                for track in mi['media']['track']:
                    if track['@type'] == "Audio":
                        if track.get('Language', 'None').startswith('en'):
                            has_eng_audio = True
                        if not has_eng_audio:
                            audio_lang = mi['media']['track'][2].get('Language_String', "").upper()
            except Exception:
                pass
        else:
            for audio in meta.get('bdinfo', {}).get('audio', []):
                if audio.get('language') == 'English':
                    has_eng_audio = True
                if not has_eng_audio and meta['bdinfo']['audio']:
                    audio_lang = meta['bdinfo']['audio'][0].get('language', '').upper()
        if audio_lang:
            lang_tag = audio_lang
        return lang_tag

    async def edit_name(self, meta: Dict) -> str:
        name = meta['name']
        category = meta.get('category', '')
        type = meta.get('type', '')
        is_disc = meta.get('is_disc', '')
        alt_title = meta.get('aka', '')
        year = meta.get('year', '')
        tag = meta.get('tag', '')
        three_d = meta.get('3D', '')
        season = meta.get('season', '')
        episode = meta.get('episode', '')
        episode_title = meta.get('episode_title', '')
        part = meta.get('part', '')
        lang_tag = self.get_language_tag(meta)

        # Adjust alt_title: brackets for movie BDMV, space-separated otherwise
        if alt_title:
            if category == "MOVIE" and type == "DISC" and is_disc == "BDMV":
                    name = name.replace(alt_title, f"[{alt_title}]")

        # Insert lang_tag after 3D (movies) or season/episode/part/episode_title (TV)
        if lang_tag:
            if lang_tag not in name:
                if category == "MOVIE":
                    insert_pos = name.find(three_d) + len(three_d) + 1 if three_d in name else name.find(year) + len(year) + 1
                else:  # TV
                    after_part = name.find(part) + len(part) + 1 if part in name else -1
                    after_episode_title = name.find(episode_title) + len(episode_title) + 1 if episode_title in name else -1
                    after_episode = name.find(f"{season}{episode}") + len(f"{season}{episode}") + 1 if f"{season}{episode}" in name else -1
                    insert_pos = max(after_part, after_episode_title, after_episode, name.find(year) + len(year) + 1)
                name = f"{name[:insert_pos]}{lang_tag} {name[insert_pos:]}"

        if tag:
            if '- ' in tag:  # Remove space if present
                tag = tag.replace('- ', '-')
            name = name.replace(meta.get('tag', ''), tag)
        elif '-NOGRP' not in name:
            name += '-NOGRP'

        return ' '.join(name.split())

    async def get_cat_id(self, category_name):
        category_id = {
            'MOVIE': '1', 
            'TV': '2', 
            }.get(category_name, '0')
        return category_id

    async def get_type_id(self, type, tv_pack, video_codec, category):
        type_id = {
            'DISC': '19', 
            'REMUX': '20',
            'WEBDL': '21',
            }.get(type, '0')
        if type  == "WEBRIP":
            if video_codec == "HEVC":
                # x265 Encode
                type_id = '10'
            if video_codec == 'AV1':
                # AV1 Encode
                type_id = '14'
            if video_codec == 'AVC':
                # x264 Encode
                type_id = '15'
        if type == "ENCODE":
            if video_codec == "HEVC":
                # x265 Encode
                type_id = '10'
            if video_codec == 'AV1':
                # AV1 Encode
                type_id = '14'
            if video_codec == 'AVC':
                # x264 Encode
                type_id = '15'
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



    async def search_existing(self, meta):
        dupes = {}
        console.print("[yellow]Searching for existing torrents on site...")
        params = {
            'api_token' : self.config['TRACKERS'][self.tracker]['api_key'].strip(),
            'tmdbId' : meta['tmdb'],
            'categories[]' : await self.get_cat_id(meta['category']),
            'types[]' : await self.get_type_id(meta['type'], meta.get('tv_pack', 0), meta.get('sd', 0), meta.get('category', "")),
            'resolutions[]' : await self.get_res_id(meta['resolution']),
            'name' : ""
        }
        if meta['category'] == 'TV':
            params['name'] = f"{meta.get('season', '')}{meta.get('episode', '')}"
        if meta.get('edition', "") != "":
            params['name'] + meta['edition']
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
