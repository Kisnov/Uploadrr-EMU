# -*- coding: utf-8 -*-
import asyncio
import requests
from typing import Dict
import re
import os

import platform

from rich.prompt import Confirm
from rich.pretty import Pretty
from src.trackers.COMMON import COMMON
from src.console import console

class HUNO():
    def __init__(self, config):
        self.config = config
        self.type = 'unit3d'
        self.tracker = 'HUNO'
        self.source_flag = 'HUNO'
        self.domain = 'https://hawke.uno'
        self.search_url = f'{self.domain}/api/torrents/filter'
        self.upload_url = f'{self.domain}/api/torrents/upload'
        pid = config['TRACKERS'][self.tracker].get('PID', 'PASSKEY_HERE')
        self.announce_url = f"{self.domain}/announce/{pid}"
        self.banned_groups = [
                            "4K4U", "Bearfish", "BiTOR", "BONE", "D3FiL3R", "DTR", "ELiTE", "EVO", "eztv", "EzzRips",
                            "HashMiner", "HETeam", "HEVCBay", "HiQVE", "HR-DR", "iFT", "ION265", "iVy", "JATT", "Joy",
                            "LAMA", "MeGusta", "m3th", "MRN", "Musafirboy", "OEPlus", "Pahe.in", "PHOCiS", "PoF", "PSA",
                            "RARBG", "RMTeam", "ShieldBearer", "SiQ", "TBD", "TSP", "VXT", "WKS", "YAWNiX", "YIFY", "YTS"
                        ]
        self.allowed_imghosts = []


    async def banned_content(self, meta):
        # Reject non-HEVC ENCODE/WEBRIP
        if meta['video_codec'] != "HEVC" and meta['type'] in {"ENCODE", "WEBRIP"}:
            console.print('[bold red]Only x265/HEVC encodes are allowed')
            return True, True  # banned, and skip

        # Bitrate check
        for track in meta['mediainfo'].get("media", {}).get("track", []):
            if track.get("@type") == "Video":
                bitrate = int(track.get("BitRate", 3000000))
                bitrate_str = track.get("BitRate_String", f"{bitrate // 1000} kbps")
                if bitrate < 3000000:
                    if meta.get("unattended"):
                        console.print(f"[bold yellow]Skipped due to low bitrate ({bitrate_str}).")
                        return False, True  # not banned, but skip
                    else:
                        console.print(f"[bold yellow]This encode is bitstarved ({bitrate_str}). Upload anyway? [y/N]")
                        if not Confirm.ask("Upload anyway?", default=False):
                            return False, True  # user said no, so skip
                        return False, False  # user allowed upload
                break

        return False, False


    async def upload(self, meta):
        common = COMMON(config=self.config)
        await common.unit3d_edit_desc(meta, self.tracker)
        await common.edit_torrent(meta, self.tracker, self.source_flag, self.announce_url)
        cat_id = await self.get_cat_id(meta['category'])
        type_id = await self.get_type_id(meta)
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
        desc = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[HUNO]DESCRIPTION.txt", 'r', encoding='utf-8').read()
        open_torrent = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/[HUNO]{meta['clean_name']}.torrent", 'rb')
        nfo_file = meta.get('nfo_file', None)
        files = {'torrent': open_torrent}
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
            'type_id' : type_id,
            'resolution_id' : resolution_id,
            'tmdb' : meta['tmdb'],
            'imdb' : meta['imdb_id'].replace('tt', ''),
            'tvdb' : meta['tvdb_id'],
            'mal' : meta['mal_id'],
            'igdb' : 0,
            'anonymous' : anon,
            'stream' : await self.is_plex_friendly(meta),
            'sd' : meta['sd'],
            'keywords' : meta['keywords'],
            'season_pack': meta.get('tv_pack', 0),
            # 'featured' : 0,
            # 'free' : 0,
            # 'double_up' : 0,
            # 'sticky' : 0,
        }

        tracker_config = self.config['TRACKERS'][self.tracker]

        if 'internal' in tracker_config:
            if tracker_config['internal'] and meta['tag'] and meta['tag'][1:] in tracker_config.get('internal_groups', []):
                data['internal'] = 1
            else:
                data['internal'] = 0

        headers = {
            'User-Agent': f'Uploadrr ({platform.system()} {platform.release()})'
        }
        params = {
            'api_token': tracker_config['api_key'].strip()
        }

        if meta['debug']:
            console.print("[blue]DATA 2 SEND[/blue]:")
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

    def get_audio(self, meta):
        channels = meta.get('channels', "")
        codec = meta.get('audio', "").replace("DD+", "DDP").replace("EX", "").replace("Dual-Audio", "").replace(channels, "")
        dual = "Dual-Audio" in meta.get('audio', "")
        language = ""

        if dual:
            language = "DUAL"
        elif 'mediainfo' in meta:
            language = next(x for x in meta["mediainfo"]["media"]["track"] if x["@type"] == "Audio").get('Language_String', "English")
            language = re.sub(r'\(.+\)', '', language)

        return f'{codec} {channels} {language}'
    
    def get_basename(self, meta):
        path = next(iter(meta['filelist']), meta['path'])
        return os.path.basename(path)

    async def edit_name(self, meta: Dict) -> str:
        basename = self.get_basename(meta)  # For Hybrid/DS4K/RM4K checks
        aka = meta.get('aka', '')
        audio = meta.get('audio')
        episode = meta.get('episode', '')
        hc = meta.get('hardcoded_subs', False)
        hdr = meta.get('hdr', 'SDR')  # Default to SDR if empty
        repack = meta.get('repack', '')
        resolution = meta.get('resolution', '')
        season = meta.get('season', '')
        service = meta.get('service', '')
        source = meta.get('source', '')
        tag = meta.get('tag', '')
        year = meta.get('year', '')
        search_year = meta.get('search_year', year) if meta['category'] == 'TV' else year

        name = meta['name'].replace(aka, '')

        # Adjust Audio
        name = name.replace(audio, self.get_audio(meta))

        # Adjust year + Ensubbed
        huno_year = f'({year}) Ensubbed' if hc else f'({year})'
        huno_search_year = f'({search_year} Ensubbed)' if hc else f'({search_year})'
        if year and year in name:
            name = name.replace(year, huno_year)
        elif search_year and search_year in name:  # For TV with search_year
            name = name.replace(search_year, huno_search_year)

        # Ajust Tag
        if tag:
            tag_with_space = tag.replace('-', ' - ')
            name = name.replace(tag, tag_with_space)
        elif '-NOGRP' not in name:  # Default tag if none present
            name += ' -NOGRP'

        # Ensure HDR is present (add SDR if missing)
        if hdr not in name and 'HDR' not in name.upper():
            audio_pos = name.rfind(' ') + 1
            if '- ' in name[audio_pos:]:
                tag_pos = name.rfind('- ')
                name = f"{name[:tag_pos]}{hdr} {name[tag_pos:]}"
            else:
                name += f" {hdr}"

        # Add Hybrid/DS4K/RM4K based on basename
        hybrid = "Hybrid" if "HYBRID" in basename.upper() else ""
        scale = "DS4K" if "DS4K" in basename.upper() else "RM4K" if "RM4K" in basename.upper() else ""
        if hybrid and "Hybrid" not in name:
            name = name.replace(source, f"{source} {hybrid}") if source in name else f"{name} {hybrid}"
        if scale and scale not in name:
            name = name.replace(resolution, f"{resolution} {scale}") if resolution in name else f"{name} {scale}"

        if repack:
            name = name.replace(repack, '').strip()
            name = f"{name} [{repack}]"

        metadata_fields = [resolution, source, service, season + episode]
        if any(f in name for f in metadata_fields):
            # Find segment after title/year and before tag
            start = name.find(')') + 2  # After (year)
            end = len(name)
            if repack and f'[{repack}]' in name:
                end = name.rfind(f'[{repack}]') - 1
            elif '- ' in name:
                end = name.rfind('- ') - 1
            metadata_segment = name[start:end].strip()
            if metadata_segment:
                name = f"{name[:start]}({metadata_segment}){name[end:]}"

        name = name.replace(': ', '- ')
        name = ' '.join(name.split())

        return name


    async def get_cat_id(self, category_name):
        category_id = {
            'MOVIE': '1',
            'TV': '2',
            }.get(category_name, '0')
        return category_id


    async def get_type_id(self, meta):
        basename = self.get_basename(meta)
        type = meta['type']

        if type == 'REMUX':
            return '2'
        elif type in ('WEBDL', 'WEBRIP'):
            return '15' if 'x265' in basename else '3'
        elif type in ('ENCODE', 'HDTV'):
            return '15'
        elif type == 'DISC':
            return '1'
        else:
            return '0'


    async def get_res_id(self, resolution):
        resolution_id = {
            'Other':'10',
            '4320p': '1',
            '2160p': '2',
            '1080p': '3',
            '1080i':'4',
            '720p': '5',
            '576p': '6',
            '576i': '7',
            '480p': '8',
            '480i': '9'
            }.get(resolution, '10')
        return resolution_id


    async def is_plex_friendly(self, meta):
        lossy_audio_codecs = ["AAC", "DD", "DDP", "OPUS"]

        if any(l in meta["audio"] for l in lossy_audio_codecs):
            return 1

        return 0


    async def search_existing(self, meta):
        dupes = {}
        console.print("[yellow]Searching for existing torrents on site...")

        params = {
            'api_token' : self.config['TRACKERS']['HUNO']['api_key'].strip(),
            'tmdbId' : meta['tmdb'],
            'categories[]' : await self.get_cat_id(meta['category']),
            'types[]' : await self.get_type_id(meta),
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
