import asyncio
import json
import itertools
import ntpath
import os
import re
import shutil
import sys
import time
import traceback
import urllib
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

import aiofiles
import anitopy
import requests
import tmdbsimple as tmdb
from guessit import guessit
from pymediainfo import MediaInfo
from tinytag import TinyTag

from src.args import Args
from src.console import console, log
from src.databases import IMDbHandler, TMDbHandler, is_scene, search_tvmaze
from src.discogs import DiscogsAPI
from src.discparse import DiscParse
from src.musicbrainz import MusicBrainzAPI
from src.screenshots import ScreenshotManager
from src.trackers.BLU import BLU
from src.trackers.COMMON import COMMON
from src.trackers.HDB import HDB
from src.trackers.PTP import PTP


class Prep():
    """
    Prepare for upload:
        Mediainfo/BDInfo
        Screenshots
        Database Identifiers (TMDB/IMDB/MAL/etc)
        Create Name
    """
    base_dir=os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
                                             
    def __init__(self, config: Dict):
        self.config = config
        self.screenshot_manager = ScreenshotManager(config=config, base_dir=self.base_dir)
        tmdb.API_KEY = config['DEFAULT']['tmdb_api']
        self._regions = None
        self._distributor_list = None
        self._services = None
        self.TMDb = TMDbHandler(config)
        self.IMDb = IMDbHandler()

    async def gather_prep(self, meta, mode):
        #Initialize gather
        meta.update(
            mode=mode,
            isdir=os.path.isdir(meta['path']),
            base_dir=self.base_dir,
            is_music=False,
            is_video=False,
            filelist={},
            user_images=[],
            log_files=[],
            nfo_file='',
            uuid=meta.get('uuid') or os.path.basename(meta['path'])
        )

        # Create temp directory
        tmp_dir = Path(f"{meta['base_dir']}/tmp/{meta['uuid']}")
        await asyncio.to_thread(tmp_dir.mkdir, parents=True, exist_ok=True)

        log.debug(f"[cyan]ID: {meta['uuid']}")

        # File collection and initial processing
        if meta['isdir']:
            meta = await self._process_directory(meta, mode)
        else:
            meta = await self._process_single_file(meta, mode)

        if meta['is_music']:
            meta = await self.gen_desc(meta)
            return meta

        # Video processing
        console.print('[red]Processing as video')
        meta = await self._process_video_metadata(meta)

        meta = await self.gen_desc(meta)
        return meta

    async def _process_directory(self, meta, mode):
        """Process directory contents recursively."""
        def collect_files_recursive(directory, meta):
            return {
                f"{path.parent.name}/{path.name}" if path.parent.name != meta['uuid'] else f"BASE/{path.name}": str(path)
                for path in Path(directory).rglob('*')
                if path.is_file() and not path.name.startswith('._')
            }

        try:
            all_files = await asyncio.to_thread(collect_files_recursive, meta['path'], meta)
            base_name = os.path.basename(meta['path'])
            nfo_candidates = []

            for file_name, full_path in all_files.items():
                ext = os.path.splitext(file_name)[1].lower()
                if ext in ['.aac', '.ac3', '.alac', '.eac3', '.flac', '.m4a', '.mp3', '.ogg', '.ogm', '.oga', '.ogv', '.ogx', '.opus', '.spx', '.wav']:
                    meta['filelist'][file_name] = full_path
                    meta['is_music'] = True
                elif ext in ['.jpg', '.jpeg', '.png', '.svg', '.ico', '.gif']:
                    meta['user_images'].append(full_path)
                elif ext == '.nfo':
                    nfo_candidates.append((file_name, full_path))
                elif ext == '.log':
                    meta['log_files'].append(full_path)

            if nfo_candidates:
                for file_name, full_path in nfo_candidates:
                    if file_name == f"{base_name}.nfo":
                        meta['nfo_file'] = full_path
                        break
                else:
                    meta['nfo_file'] = nfo_candidates[0][1]

            if not meta['is_music']:
                video_extensions = ['.mkv', '.mp4', '.ts']
                meta['is_video'] = any(full_path.lower().endswith(tuple(video_extensions)) for full_path in all_files.values())

            if meta['is_music']:
                console.print('[red]Processing as music')
                await self.get_music(meta, mode)

        except Exception as e:
            log.error(f"Error processing directory: {e}")
        return meta

    async def _process_single_file(self, meta, mode):
        """Process a single file."""
        file_extension = os.path.splitext(meta['path'])[1].lower()
        if file_extension in ['.aac', '.alac', '.flac', '.m4a', '.mp3', '.opus', '.wav']:
            meta['is_music'] = True
            console.print('[red]Processing as music')
            await self.process_single_music_file(meta, mode)
        elif file_extension in ['.mkv', '.mp4', '.ts']:
            meta['is_video'] = True
            base_name = os.path.splitext(meta['path'])[0]
            match_nfo = f"{base_name}.nfo"
            if await asyncio.to_thread(os.path.exists, match_nfo):
                meta['nfo_file'] = match_nfo
            elif not await asyncio.to_thread(os.path.exists, match_nfo) and meta.get('nfo'):
                directory = os.path.dirname(meta['path'])
                nfo_files = await asyncio.to_thread(lambda: [f for f in os.listdir(directory) if f.endswith('.nfo')])
                if nfo_files:
                    meta['nfo_file'] = os.path.join(directory, nfo_files[0])
        return meta

    async def _process_video_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Process video-specific metadata"""
        # Get disc info
        meta['is_disc'], videoloc, bdinfo, meta['discs'] = await self.get_disc(meta)
        meta['filelist'] = []
        meta['bdinfo'] = video = videopath = filename = mi = None
        meta['is_sports'] = False

        # Process based on disc type
        if meta['is_disc'] == "BDMV":
            video, meta['scene'], meta['imdb'] = await is_scene(meta['path'], meta.get('imdb'))
            try:
                title = meta['discs'][0]['bdinfo']['title']
                guess_name = re.sub(r'Blu-ray™|-\s*|Vol\.\s*\d+', '', title).strip()
                log.debug(f"guess_name: {guess_name}")
                filename = guessit(re.sub(r"[^0-9a-zA-Z\[\]]+", " ", guess_name), {"excludes": ["country", "language"]})['title']
                meta['search_year'] = guessit(bdinfo['title']).get('year', "")
            except Exception:
                guess_name = bdinfo['label'].replace('-', ' ')
                filename = guessit(re.sub(r"[^0-9a-zA-Z\[\]]+", " ", guess_name), {"excludes": ["country", "language"]})['title']
                meta['search_year'] = guessit(bdinfo['label']).get('year', "")
            meta['resolution'] = self.mi_resolution(bdinfo['video'][0]['res'], guessit(video), width="OTHER", scan="p", height="OTHER", actual_height=0)
            meta['bdinfo'] = bdinfo

        elif meta['is_disc'] == "DVD":
            disc_path = meta['discs'][0]['path']
            video, meta['scene'], meta['imdb'] = await is_scene(meta['path'], meta.get('imdb'))
            guess_name = disc_path.replace('-', ' ')
            filename = guessit(guess_name, {"excludes": ["country", "language"]})['title']
            meta['search_year'] = guessit(video).get('year', "")
            
            if not meta.get('edit', False):
                tmp_dir = Path(meta['base_dir']) / 'tmp' / meta['uuid']
                title_set = meta['discs'][0]['main_set'][0][:2]
                vob_path = f"{disc_path}/VTS_{title_set}_1.VOB"
                ifo_path = f"{disc_path}/VTS_{title_set}_0.IFO"
                ifo_tmp = tmp_dir / f"VTS_{title_set}_0.IFO"

                mi = await self.exportInfo(vob_path, False, meta['uuid'], meta['base_dir'], export_text=True)

                # DVD version check and patch if not already done
                text_file = tmp_dir / "MEDIAINFO.txt"
                version = "Unknown"
                if text_file.exists() and not ifo_tmp.exists():
                    with open(text_file, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                        if lines:
                            last_line = lines[-1].strip()
                            match = re.search(r'MediaInfoLib - v(\d+\.\d+)', last_line)
                            if match:
                                version = match.group(1)
                                log.debug(f"Detected MediaInfoLib version: {version}")

                if version != "Unknown":
                    major, minor = map(int, version.split('.'))
                    if (major > 23) or (major == 23 and minor > 6) and os.path.exists(ifo_path):
                        try:
                            shutil.copy2(ifo_path, ifo_tmp)
                            log.debug("[bold green]DVD Language Patched - Using IFO for text output")
                            mi = await self.exportInfo(ifo_tmp, False, meta['uuid'], meta['base_dir'], export_text=True, dvd_patch=True)
                        except Exception as e:
                            log.error(f"[bold red]Failed to copy IFO file {ifo_path}: {e}")
                meta['dvd_size'] = await self.get_dvd_size(meta['discs'])

        elif meta['is_disc'] == "HDDVD":
            video, meta['scene'], meta['imdb'] = await is_scene(meta['path'], meta.get('imdb'))
            guess_name = meta['discs'][0]['path'].replace('-', '')
            filename = guessit(guess_name, {"excludes": ["country", "language"]})['title']
            meta['search_year'] = guessit(meta['discs'][0]['path']).get('year', "")
            if not meta.get('edit', False):
                mi = await self.exportInfo(video, False, meta['uuid'], meta['base_dir'], export_text=False)

        else:  # Non-disc case
            videopath, meta['filelist'] = self.get_video(videoloc, meta.get('mode', 'discord'))
            video, meta['scene'], meta['imdb'] = await is_scene(videopath, meta.get('imdb'))
            guess_name = ntpath.basename(video).replace('-', ' ')
            filename = guessit(re.sub(r"[^0-9a-zA-Z\[\]]+", " ", guess_name), {"excludes": ["country", "language"]}).get(
                "title", guessit(re.sub("[^0-9a-zA-Z]+", " ", guess_name), {"excludes": ["country", "language"]})["title"]
            )
            meta['search_year'] = guessit(video).get('year', "")
            if not meta.get('edit', False):
                mi = await self.exportInfo(videopath, meta.get('isdir', False), meta['uuid'], meta['base_dir'], export_text=True)

        # Common processing
        meta['video'] = video
        meta['resolution'] = meta.get('resolution') or self.get_resolution(guessit(meta['video']), meta['uuid'], meta['base_dir'])
        meta['sd'] = self.is_sd(meta['resolution'])

        if self.is_sports(meta['uuid']):
            meta['is_sports'] = True
            meta['category'] = 'SPORTS'
        
        meta['mediainfo'] = mi
        meta['filename'] = filename.split(' AKA ')[0] if " AKA " in filename.replace('.', ' ') else filename

        bdinfo = meta['bdinfo']
        video = meta['video']
        filename = meta['filename']

        # Tracker API integrations
        await self._integrate_tracker_apis(meta)

        if not meta['category']:
            meta['category'] = self.get_cat(video)

        if meta['category'] in ("TV", "MOVIE"):
            meta['tmdb'] = meta.get('tmdb_manual', meta.get('tmdb'))  # Use tmdb_manual if available
            meta['type'] = meta.get('type') or self.get_type(video, meta['scene'], meta['is_disc'])

            if not meta.get('imdb') and meta.get('imdb_id'):
                meta['imdb'] = f"tt{meta['imdb_id']}"
            if not meta.get('tmdb') and not meta.get('imdb'):
                meta['category'], meta['tmdb'], meta['imdb'] = self.get_tmdb_imdb_from_mediainfo(
                    mi, meta['category'], meta['is_disc'], meta['tmdb'], meta['imdb']
                )
            if not meta.get('tmdb') and not meta.get('imdb'):
                meta = await self.TMDb.get_tmdb_id(meta['filename'], meta['search_year'], meta, meta['category'], meta['filename'])

            elif meta.get('imdb') and not meta.get('tmdb_manual'):
                meta['imdb_id'] = str(meta['imdb']).replace('tt', '')
                meta = await self.TMDb.get_tmdb_from_imdb(meta, meta['filename'])

            else:
                meta['tmdb_manual'] = meta.get('tmdb')

            # If unattended mode is on, and TMDb ID is missing or invalid
            if meta.get('unattended') and (meta.get('tmdb') is None or int(meta['tmdb']) == 0):
                meta['tmdb_not_found'] = True
                console.print(f"[red]Unable to find TMDb match for {Path(meta['uuid']).stem}")
                return meta

            meta = await (self.IMDb.imdb_other_meta(meta) if int(meta['tmdb']) == 0 else self.TMDb.tmdb_other_meta(meta))
            meta['tvmaze_id'], meta['imdb_id'], meta['tvdb_id'] = await search_tvmaze(meta['filename'], meta['search_year'], meta.get('imdb_id', '0'), meta.get('tvdb_id', 0))
            meta['imdb_id'] = meta.get('imdb_id') or await self.IMDb.search_imdb(meta['filename'], meta['search_year'])
            if not meta.get('imdb_info') and int(meta['imdb_id']) != 0:
                meta['imdb_info'] = await self.IMDb.get_imdb_info(meta['imdb_id'], meta)

        meta['tag'] = meta.get('tag') or self.get_tag(video, meta)
        if meta['tag'] and not meta['tag'].startswith('-'):
            meta['tag'] = f"-{meta['tag']}"
        if meta['category'] == 'TV':    
            meta = await self.get_season_episode(video, meta)
        meta = await self.tag_override(meta)

        meta['video'] = video
        meta['audio'], meta['channels'], meta['has_commentary'] = self.get_audio_v2(mi, meta, bdinfo)
        if meta['tag'][1:].startswith(meta['channels']):
            meta['tag'] = meta['tag'].replace(f"-{meta['channels']}", '')
        if meta.get('no_tag', False):
            meta['tag'] = ""
        meta['3D'] = self.is_3d(mi, bdinfo)
        meta['source'], meta['type'] = self.get_source(meta['type'], video, meta['path'], meta['is_disc'], meta)
        if meta['type'] in ('WEBDL', 'WEBRIP', 'HDTV'):
            meta['service'], meta['service_longname'] = self.get_service(video, meta.get('tag', ''), meta['audio'], meta['filename']) if meta.get('service') in (None, '') else (meta['service'], meta['service_longname'])
        meta['uhd'] = self.get_uhd(meta['type'], guessit(meta['path']), meta['resolution'], meta['path'])
        meta['hdr'] = self.get_hdr(mi, bdinfo)
        meta['distributor'] = self.get_distributor(meta['distributor'])
        if meta.get('is_disc') == "BDMV":
            meta['region'] = self.get_region(bdinfo, meta.get('region'))
            meta['video_codec'] = self.get_video_codec(bdinfo)
        else:
            meta['video_encode'], meta['video_codec'], meta['has_encode_settings'], meta['bit_depth'] = self.get_video_encode(mi, meta['type'], bdinfo)
        if meta['category'] == 'SPORTS':
            if meta['scene']:
                meta['name'] = meta['title'] = Path(video).stem
            else:
                meta['name'] = meta['title'] = Path(meta['uuid']).stem

        meta['edition'], meta['repack'], meta['cut'], meta['ratio'] = self.get_edition(meta['title'], meta['path'], bdinfo, meta['filelist'], meta.get('manual_edition'))
        if "REPACK" in meta.get('edition', ""):
            meta['repack'] = re.search(r"REPACK[\d]?", meta['edition'])[0]
            meta['edition'] = re.sub(r"REPACK[\d]?", "", meta['edition']).strip().replace('  ', ' ')

        meta['stream'] = self.stream_optimized(meta.get('stream', False))
        meta['anon'] = self.is_anon(meta.get('anon', False))

        return meta

    def get_tmdb_imdb_from_mediainfo(self, mediainfo, category, is_disc, tmdbid, imdbid):
            """Extract TMDb and IMDb IDs from mediainfo metadata."""
            if not is_disc and mediainfo['media']['track'][0].get('extra'):
                extra = mediainfo['media']['track'][0]['extra']
                for key in extra:
                    if key.lower().startswith('tmdb'):
                        parser = Args(config=self.config)
                        category, tmdbid = parser.parse_tmdb_id(id=extra[key], category=category)
                    if key.lower().startswith('imdb'):
                        try:
                            imdbid = str(int(extra[key].replace('tt', ''))).zfill(7)
                        except (ValueError, TypeError):
                            log.debug(f"Invalid IMDb ID in mediainfo: {extra[key]}")
                            pass
            return category, tmdbid, imdbid

    async def _integrate_tracker_apis(self, meta):
        """Integrate with tracker APIs."""
        if str(self.config['TRACKERS'].get('PTP', {}).get('useAPI')).lower() == "true":
            ptp = PTP(config=self.config)
            if meta.get('ptp'):
                meta['ptp_manual'] = meta['ptp']
                meta['imdb'], meta['ext_torrenthash'] = await ptp.get_imdb_from_torrent_id(meta['ptp'])
            else:
                search_file_folder = 'file' if meta['is_disc'] in [None, ""] else 'folder'
                ptp_search_term = os.path.basename(meta['filelist'][0] if meta['is_disc'] in [None, ""] else meta['path'])
                ptp_imdb, ptp_id, meta['ext_torrenthash'] = await ptp.get_ptp_id_imdb(ptp_search_term, search_file_folder)
                if ptp_imdb:
                    meta['imdb'] = ptp_imdb
                if ptp_id:
                    meta['ptp'] = ptp_id

        if str(self.config['TRACKERS'].get('HDB', {}).get('useAPI')).lower() == "true":
            hdb = HDB(config=self.config)
            if not meta.get('ptp') or meta.get('hdb'):
                hdb_id = meta.get('hdb')
                if hdb_id:
                    meta['hdb_manual'] = hdb_id
                    hdb_imdb, hdb_tvdb, meta['hdb_name'], meta['ext_torrenthash'] = await hdb.get_info_from_torrent_id(hdb_id)
                elif meta['is_disc'] in [None, ""]:
                    hdb_imdb, hdb_tvdb, meta['hdb_name'], meta['ext_torrenthash'], hdb_id = await hdb.search_filename(meta['filelist'])
                if hdb_imdb:
                    meta['imdb'] = str(hdb_imdb)
                if hdb_tvdb:
                    meta['tvdb_id'] = str(hdb_tvdb)
                if hdb_id:
                    meta['hdb'] = hdb_id

        if str(self.config['TRACKERS'].get('BLU', {}).get('useAPI')).lower() == "true":
            blu = BLU(config=self.config)
            if meta.get('blu'):
                meta['blu_manual'] = meta['blu']
                blu_tmdb, blu_imdb, blu_tvdb, blu_mal, blu_desc, blu_category, meta['ext_torrenthash'], blu_imagelist = await COMMON(self.config).unit3d_torrent_info("BLU", blu.torrent_url, meta['blu'])
                if blu_tmdb not in [None, '0']:
                    meta['tmdb_manual'] = blu_tmdb
                if blu_imdb not in [None, '0']:
                    meta['imdb'] = str(blu_imdb)
                if blu_tvdb not in [None, '0']:
                    meta['tvdb_id'] = blu_tvdb
                if blu_mal not in [None, '0']:
                    meta['mal'] = blu_mal
                if blu_desc not in [None, '0', '']:
                    meta['blu_desc'] = blu_desc
                if blu_category.upper() in ['MOVIE', 'TV SHOW', 'FANRES']:
                    meta['category'] = 'TV' if blu_category.upper() == 'TV SHOW' else blu_category.upper()
                if not meta.get('image_list', []):
                    meta['image_list'] = blu_imagelist

    def load_json(self, filename): #Lazy-Load json's
            filepath = os.path.join(self.base_dir, "data", filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except FileNotFoundError:
                raise FileNotFoundError(f"Could not find JSON file: {filepath}")
            except json.JSONDecodeError:
                raise ValueError(f"Invalid JSON format in file: {filepath}")

    @property
    def regions(self):
        if self._regions is None:
            self._regions = self.load_json("regions.json")
        return self._regions

    @property
    def distributor_list(self):
        if self._distributor_list is None:
            self._distributor_list = set(self.load_json("distributors.json"))
        return self._distributor_list

    @property
    def services(self):
        if self._services is None:
            self._services = self.load_json("services.json")
        return self._services

    """
    Determine if disc and if so, get bdinfo
    """
    async def get_disc(self, meta):
        is_disc = None
        videoloc = meta['path']
        bdinfo = None
        bd_summary = None
        discs = []
        parse = DiscParse()
        for path, directories, files in os. walk(meta['path']):
            for each in directories:
                if each.upper() == "BDMV": #BDMVs
                    is_disc = "BDMV"
                    disc = {
                        'path' : f"{path}/{each}",
                        'name' : os.path.basename(path),
                        'type' : 'BDMV',
                        'summary' : "",
                        'bdinfo' : ""
                    }
                    discs.append(disc)
                elif each == "VIDEO_TS": #DVDs
                    is_disc = "DVD"
                    disc = {
                        'path' : f"{path}/{each}",
                        'name' : os.path.basename(path),
                        'type' : 'DVD',
                        'vob_mi' : '',
                        'ifo_mi' : '',
                        'main_set' : [],
                        'size' : ""
                    }
                    discs.append(disc)
                elif each == "HVDVD_TS":
                    is_disc = "HDDVD"
                    disc = {
                        'path' : f"{path}/{each}",
                        'name' : os.path.basename(path),
                        'type' : 'HDDVD',
                        'evo_mi' : '',
                        'largest_evo' : ""
                    }
                    discs.append(disc)
        if is_disc == "BDMV":
            if meta.get('edit', False) is False:
                discs, bdinfo = await parse.get_bdinfo(discs, meta['uuid'], meta['base_dir'], meta.get('discs', []))
            else:
                discs, bdinfo = await parse.get_bdinfo(meta['discs'], meta['uuid'], meta['base_dir'], meta['discs'])
        elif is_disc == "DVD":
            discs = await parse.get_dvdinfo(discs)
            export = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt", 'w', newline="", encoding='utf-8')
            export.write(discs[0]['ifo_mi'])
            export.close()
        elif is_disc == "HDDVD":
            discs = await parse.get_hddvd_info(discs)
            export = open(f"{meta['base_dir']}/tmp/{meta['uuid']}/MEDIAINFO.txt", 'w', newline="", encoding='utf-8')
            export.write(discs[0]['evo_mi'])
            export.close()
        discs = sorted(discs, key=lambda d: d['name'])
        return is_disc, videoloc, bdinfo, discs



    """
    Get video files

    """

    def get_video(self, videoloc, mode):
        filelist = []
        videoloc = os.path.normpath(os.path.abspath(videoloc))
        video_extensions = ['.mkv', '.mp4', '.ts']
        
        if os.path.isdir(videoloc):
            for file in os.listdir(videoloc):
                full_path = os.path.join(videoloc, file)
                if not file.startswith('._'):
                    if os.path.isfile(full_path) and any(file.lower().endswith(ext) for ext in video_extensions):
                        if not (file.lower().endswith('sample.mkv') or file.lower().startswith('!sample')):
                            filelist.append(full_path)

            if filelist:
                video = sorted(filelist)[0]
            else:
                console.print(f"[red]No video files found in {videoloc}")
                if mode == 'cli':
                    exit()
                return None, []
        else:
            video = videoloc
            if not os.path.basename(videoloc).startswith('._') and os.path.splitext(videoloc)[1].lower() in video_extensions:
                filelist.append(videoloc)
        
        return video, filelist

    async def get_music(self, meta, mode):
        log.ddebug("Starting get_music")
        meta['is_music'] = True
        # base_dir = meta['base_dir']
        musicloc = meta['path']
        
        console.print(f"[yellow]Checking music directory: {musicloc}")
        
        # Sort tracks, covers, and proof
        tracks, cover, back_cover, proof = self.sort_music(meta, musicloc)
        meta.update(
            track_count=len(tracks),
            user_cover=cover,
            back_cover=back_cover,
            user_proof=proof,
        )
        
        if not self.has_sufficient_music_metadata(meta):
            await self.process_music(meta, self.base_dir, tracks)
            log.debug("Music processing completed")
        
        meta = await self.gen_desc(meta)
        
        return meta

    """SORT MUSIC"""

    def sort_music(self, meta, musicloc):
        cover, back_cover, proof = None, None, None
        tracks = []

        has_disc_folders = any(
            any(folder_name.lower() in key.lower() for folder_name in ['disc', 'cd', 'record'])
            for key in meta['filelist'].keys()
        ) and not any(key.startswith("BASE/") for key in meta['filelist'].keys())

        log.debug(f"[blue]Has disc folders: {has_disc_folders}")

        for key, path in meta['filelist'].items():
            if isinstance(key, str):
                log.debug(f"[magenta]Processing file: {key}")

                if key.startswith("BASE/"):
                    file = key.replace("BASE/", "")
                    track_match = re.match(r'([A-Z]|\d+)(?:[\s.-])?(.*)', file, re.IGNORECASE)
                    if track_match:
                        track_num = track_match.group(1)
                        rest_of_file = track_match.group(2).strip()
                    else:
                        log.warning(f"[yellow]Could not extract track number from: {file}")
                        continue

                    if len(track_num) == 3 and track_num.isdigit():
                        disc_num = track_num[0]
                        disc = f"Disc {int(disc_num)}"
                        track = track_num[1:]
                        file_parts = file.split('-')
                        file = '-'.join([track] + file_parts[1:])
                        log.debug(f"[yellow]Detected multi-disc album: {disc}, File: {file}")
                    else:
                        disc = "Disc 1"
                        log.debug(f"[yellow]Detected single disc album: {disc}, File: {file}")

                else:
                    disc, file = key.split('/', 1) if '/' in key else ("Disc 1", key)

                ext = os.path.splitext(file)[1].lower()

                if ext in ['.aac', 'ac3', '.alac', '.flac', '.m4a', '.mp3', '.ogg', '.ogm', '.oga', '.ogv', '.ogx', '.opus', '.spx', '.wav']:
                    if not (file.lower().endswith('sample.mp3') or file.lower().startswith('!sample')):
                        if len(file.split()[0]) > 2 and file.split()[0].isdigit():
                            file = re.sub(r'^\d{3}-', '01-', file)
                            log.debug(f"[purple]Adjusted file for multi-disc: {file}")
                        tracks.append((f"{disc}", file, path))
                        log.debug(f"[green]Added music file: {disc}, File: {file}")

            else:
                disc = "Disc 1"
                file = key

        # Check for alphanumeric track numbers to set source and adjust disc label
        has_alpha_tracks = any(re.match(r'^[A-Z]\d*$', os.path.splitext(x[1])[0].split()[0], re.IGNORECASE) for x in tracks)
        if has_alpha_tracks:
            meta['source'] = 'VINYL'
            tracks = [(disc.replace("Disc 1", "Record"), file, path) for disc, file, path in tracks]
        
        #Sort tracks
        def sort_key(x):
            disc_num = re.search(r'\d+', x[0].split()[-1])
            disc_number = int(disc_num.group()) if disc_num else 1  # Default to 1 if no number found
            
            track_part = os.path.splitext(x[1])[0].split()[0]
            match = re.match(r'^([A-Z])?(\d+)(?:[\s\.\-_\,](\d+))?', track_part, re.IGNORECASE)
            
            if match:
                letter, number, sub_number = match.groups()
                primary = (ord(letter.upper()) - ord('A') + 1) * 1000 if letter else 0
                secondary = int(number or '0') * 10
                tertiary = int(sub_number or '0')
                return (disc_number, primary + secondary + tertiary)
            elif track_part.isdigit():
                return (disc_number, int(track_part))
            else:
                return (disc_number, 999)


        # Handle cover and proof images

        front_cover = ['front', 'cover', 'album', 'disc1', 'cd1']
        back_covers = ['back_cover', 'rear', 'backside', 'back']

        for path in meta['user_images']:
            file = os.path.basename(path).lower().replace(' ', '')

            if 'proof' in file:
                proof = path
                log.debug(f"[magenta]Assigned proof image: {proof}")
                continue

            ext = os.path.splitext(file)[1].lower()
            if ext not in ['.jpg', '.jpeg']:
                continue

            if any(variant in file for variant in back_covers):
                back_cover = path
                log.debug(f"[yellow]Assigned back cover image: {back_cover}")
                continue

            if cover is None:
                if any(variant in file for variant in front_cover):
                    cover = path
                    log.debug(f"[cyan]Found cover image: {cover}")
                    continue

            if cover is None and 'proof' not in file:
                cover = path
                log.debug(f"[cyan]Fallback cover image: {cover}")

        tracks.sort(key=sort_key)
        log.debug(f"[blue]Sorted tracks: {tracks}")
        return tracks, cover, back_cover, proof


    #SINGLES NEED TO BE REVAMPED TO NEW STRUCTURE
    async def process_single_music_file(self, meta, mode):
        musicloc = meta['path']
        if not os.path.basename(musicloc).startswith('._'):
            meta['filelist'] = {musicloc: musicloc}
            meta['track_count'] = 1
            
            parent_dir = os.path.dirname(musicloc)
            for entry in os.listdir(parent_dir):
                if not entry.startswith('._'):
                    full_path = os.path.join(parent_dir, entry)
                    if os.path.isfile(full_path):
                        if entry.lower() in ('cover.jpg', 'cover.jpeg', 'front.jpg', 'front.jpeg', 'album.jpg', 'album.jpeg'):
                            meta['user_cover'] = full_path
                            break  # Only need one cover
                        elif entry.lower().endswith(('.jpg', '.jpeg')) and 'proof' in entry.lower():
                            meta['user_proof'] = full_path
                            break  # Only need one proof, likely not needed as singles mostly digital

            if not self.has_sufficient_music_metadata(meta) or meta.get('mbid_manual'):
                await self.process_music(meta, meta['base_dir'])
            
            meta = await self.gen_desc(meta)
        return meta


    """
    Process Music

    """
    def has_sufficient_music_metadata(self, meta):
        required_keys = ['artist', 'album', 'year', 'source', 'type', 'bitrate', 'duration', 'mbid', 'album_cover']
        return all(meta.get(key, None) for key in required_keys)

    async def process_music(self, meta, base_dir, tracks):  
        log.debug("Starting process_music") 
        mb = MusicBrainzAPI("Art2Album / 1.0")

        if self.has_sufficient_music_metadata(meta):
            return meta

        #INITIALIZE META 
        log.debug('About to first_audio_file')
        first_audio_file = tracks[0][2]
        mi = self.exportInfo(first_audio_file, meta['isdir'], meta['uuid'], self.base_dir, export_text=True)
        meta.update(
            is_disc=False,
            screens=0,
            edition='',
            bdinfo=None,
            tmdb=0,
            imdb=0,
            tvdb_id=0,
            mal_id=0,
            sd=0,
            keywords='',
            category="MUSIC",
            mediainfo=mi
        )

        album_fname = meta['uuid']   
        album_fname = album_fname.replace('&', 'AANDD')
        normalized_af = re.sub(r'[^a-zA-Z0-9\sAANDD]', ' ', album_fname)
        normalized_af = normalized_af.replace('AANDD', '&')
        meta['source'] = meta.get('format', self.get_music_source(normalized_af))
        user_tag = meta.get('tag')
        meta['tag'] = user_tag if user_tag else self.extract_tag(album_fname)

        track = next((track for track in mi['media']['track'] if track['@type'] == 'General'), None)
        if track:
            meta['type'] = (track.get('Format').replace('MPEG Audio', 'MP3').replace('-', '')).upper()
            meta['duration'] = track.get('Duration', 'Unknown')
            meta['artist'] = track.get('Album_Performer', track.get('Performer', None))
            meta['album'] = track.get('Album', None)
            recorded_date = track.get('Recorded_Date', track.get('Released_Date', track.get('Year', None)))
            if recorded_date and '-' in recorded_date:
                meta['year'] = recorded_date.split('-')[0]
                meta['release_date'] = recorded_date
            else:
                meta['year'] = recorded_date
                meta['release_date'] = ''
            extra = track.get('extra', {}) if track else {}
            meta['artist'] = meta['artist'] or extra.get('ARTISTS', extra.get('Performer', None))
            meta['album'] = meta['album'] or extra.get('Album', None)
            meta['year'] = meta['year'] or extra.get('Year', extra.get('Recorded_Date', extra.get('Released_Date', None)))
            
            meta['barcode'] = next(
                (value for key, value in track.items() if key.lower() in ['barcode', 'upc', 'scanid', 'scan_id']), 
                next((value for key, value in extra.items() if key.lower() in ['barcode', 'upc', 'scanid', 'scan_id']), None)
            )

        audio_track = next((track for track in mi['media']['track'] if track['@type'] == 'Audio'), None)
        if audio_track: 
            meta['bit_depth'] = audio_track.get('BitDepth', '')
            meta['bitrate'] = audio_track.get('BitRate_String', '').replace('/', '').replace(' ', '') 

        meta['catalog_number'] = self.extract_catalog_number(meta['uuid'], extra)
        mbid =  meta.get('mbid_manual') or meta.get('mbid')
        if mbid:
            await mb.fetch_musicbrainz_mbid(mbid, meta)
        else:
            # Try different methods of searching in sequence if no MBID is present
            methods_to_try = [
                ("barcode", self.search_by_barcode),
                ("catalog_number", self.search_by_catalog_number),
                ("title_artist", self.search_by_title_artist)
            ]

            success = False
            for method_name, search_func in methods_to_try:
                if await self.try_search_method(meta, mb, method_name, search_func):
                    success = True
                    break  # Exit the loop if a successful search was performed

            if not success:
                console.print("[red]WARN[/red]: No MusicBrainz data found using any method.")                 

        await self.process_with_discogs(meta)

        await self.screenshot_uploadrr.upload_proof_or_cover(meta, 'album_cover', 'user_cover', ('.jpg', '.jpeg'))
        await self.screenshot_uploadrr.upload_proof_or_cover(meta, 'album_back', 'back_cover')
        await self.screenshot_uploadrr.upload_proof_or_cover(meta, 'album_proof', 'user_proof')

        if not meta.get('tracklist', {}):
            log.debug('[magenta]MANUAL TRACKLIST')
            tracklist = {}
            for disc, file, path in tracks:
                clean_track_name, _ = os.path.splitext(file)

                try:
                    # Get track duration using TinyTag
                    tag = TinyTag.get(path)
                    track_duration = tag.duration
                    minutes = int(track_duration // 60)
                    seconds = int(track_duration % 60)
                    time = f"{minutes:02}:{seconds:02}"

                    if disc not in tracklist:
                        tracklist[disc] = {}

                    tracklist[disc][clean_track_name] = time 

                except Exception as e:
                    log.error(f"Error reading track {file}: {e}")
                    if disc not in tracklist:
                        tracklist[disc] = {}
                    tracklist[disc][clean_track_name] = "00:00"  # Default to "00:00" if there's an error

            log.debug(f"Generated tracklist: {tracklist}")
            meta['tracklist'] = tracklist

        if not os.path.exists(f"{base_dir}/tmp/{meta['uuid']}/MediaInfo.json"):
            self.export_audio_info(tracks, meta['uuid'], meta['base_dir'])

        if 'discogs_id' in meta and meta['discogs_id']:
            meta['keywords'] += f", Discogs: {meta['discogs_id']}"

        if 'catalog_number' in meta and meta['catalog_number']:
            meta['keywords'] += f", CATNO: {meta['catalog_number']}"

        if 'barcode' in meta and meta['barcode']:
            meta['keywords'] += f", UPC: {meta['barcode']}"

        log.debug('Finishing process_music, retunring meta')
        return meta

    async def try_search_method(self, meta, mb, method_name, search_func):
        try:
            if method_name == "barcode":
                barcode = meta.get('barcode')
                if not barcode:
                    barcode_match = re.search(r'(barcode|upc)-([0-9]+)', meta['uuid'], re.IGNORECASE)
                    if barcode_match:
                        barcode = barcode_match.group(2)
                        meta['barcode'] = barcode
                if barcode:
                    await search_func(barcode, meta, mb)
            elif method_name == "catalog_number" and meta.get('catalog_number'):
                await search_func(meta.get('catalog_number'), meta, mb)
            elif method_name == "title_artist":
                release_title = meta.get('album', '')
                artist_name = meta.get('artist', '')
                track_count = meta.get('track_count', 0)
                if release_title and artist_name:
                    await search_func(release_title, artist_name, track_count, meta, mb)
            
            if meta.get('mbid'):  # Check if we've found an mbid
                log.info(f"MusicBrainz data found using {method_name}.")
                return True
        except Exception as e:
            log.error(f"Error occurred while trying to search by {method_name}: {e}")
        return False

    async def search_by_barcode(self, barcode, meta, mb):
        await mb.search_musicbrainz_by_barcode(barcode, meta)

    async def search_by_catalog_number(self, catalog_number, meta, mb):
        await mb.search_by_catalog_number(catalog_number, meta)

    async def search_by_title_artist(self, release_title, artist_name, track_count, meta, mb):
        await mb.search_releases_by_title_artist(release_title, artist_name, track_count, meta)


    """ 
      Music TAG Processing 
    """
    def extract_tag(self, album_fname):
        album_fname = re.sub(r'hi-res', '', album_fname, flags=re.IGNORECASE)
        last_hyphen_index = album_fname.rfind('-')

        tag_match = ''

        if last_hyphen_index != -1:
            after_last_hyphen = album_fname[last_hyphen_index + 1:].strip()
            outside_parentheses = re.sub(r'\(.*?\)', '', after_last_hyphen).strip()

            if outside_parentheses:
                bracket_tag_match = re.search(r'\[([^\[\]]+)\]$', outside_parentheses)
                if bracket_tag_match:
                    tag = bracket_tag_match.group(1).strip()

                    if self.is_valid_tag(tag):
                        tag_match = f'-{tag}'

                else:
                    tag = re.search(r'^[^-\[\]]+$', outside_parentheses)
                    if tag:
                        tag = tag.group(0).strip()

                        if self.is_valid_tag(tag):
                            tag_match = f'-{tag}'

        return tag_match if tag_match else ''

    def is_valid_tag(self, tag):
        reject_terms = ['web', 'flac', 'alac', 'mp3', 'm4a', 'wav', 'vinyl']
        if any(term in tag.lower() for term in reject_terms):
            return False
        if re.search(r'\d{2,}', tag):
            return False
        if 'khz' in tag.lower():
            return False
        if ' ' in tag: 
            return False        
        if not re.match(r'^[a-zA-Z0-9\s&]+$', tag):
            return False        
        return True


    def extract_catalog_number(self, album_fname: str, extra: Dict[str, Any]) -> Optional[str]:
        """
        Extract CATALOG_NUMBER from album_fname or MediaInfo.
        """
        EXCLUSION_KEYWORDS = ['web', 'vinyl', 'cd', 'mp3', 'flac', 'wav', 'aac', 'alac']
        LABEL_WORDS = ['records', 'label', 'music', 'productions', 'studio', 'group', 'band', 'sound', 'entertainment']
        log.debug(f"Extracting catalog number from: {album_fname}")

        catalog_number = None

        catalog_number = extra.get('CATALOG_NUMBER') or extra.get('CATALOGNUMBER') or extra.get('CATNO') or extra.get('LABELNO')
        if catalog_number:
            log.debug(f"Found catalog number in extra dictionary: {catalog_number}")
            return re.sub(r'[ -]', '', catalog_number.strip())

        catalog_match = re.search(r'\{([^}]+)\}', album_fname)
        if catalog_match:
            catalog_number = catalog_match.group(1).strip()
            log.debug(f"Found catalog number in curly braces: {catalog_number}")
            
            if any(word in catalog_number.lower() for word in LABEL_WORDS):
                log.debug(f"Excluded as label or descriptor: {catalog_number}")
                return None

        if not catalog_number:
            catalog_match = re.search(r'\{([^}]+)\}', album_fname)
            if catalog_match:
                catalog_candidate = catalog_match.group(1).strip()
                parts = [part.strip() for part in catalog_candidate.split(',') if part.strip()]
                
                for part in parts:
                    if not any(word in part.lower() for word in LABEL_WORDS):
                        catalog_number = part
                        log.debug(f"Found valid catalog number: {catalog_number}")
                        break
                else:
                    log.debug(f"No valid catalog number found from parts: {parts}")
            
            if not catalog_number:
                catalog_match_title = re.search(r'\[([A-Za-z0-9\s,]+)\]', album_fname)
                if catalog_match_title:
                    catalog_candidate = catalog_match_title.group(1).strip()
                    parts = [part.strip() for part in catalog_candidate.split(',') if part.strip()]
                    
                    for part in parts:
                        if re.search(r'\w', part) and not any(word in part.lower() for word in LABEL_WORDS):
                            catalog_number = part
                            log.debug(f"Selected valid catalog number: {catalog_number}")
                            break
                    else:
                        log.debug(f"No valid catalog number found from parts: {parts}")

        # Check for exclusion keywords in the selected catalog number
        if catalog_number and any(excl in catalog_number.lower() for excl in EXCLUSION_KEYWORDS):
            log.debug(f"Excluded due to keyword in catalog number: {catalog_number}")
            return None
        
        # Return the cleaned catalog number
        if catalog_number:
            return re.sub(r'[ -]', '', catalog_number.strip())
        
        log.debug("No catalog number found.")
        return None

    async def process_with_discogs(self, meta: Dict[str, Any]) -> None:
        """
        Query Discogs API to find metadata when barcode or MBID is missing.
        """
        dc = DiscogsAPI("Art2Album / 1.0")
        mb = MusicBrainzAPI("Art2Album / 1.0")
        discogs_id = meta.get('discogs_id') or meta.get('DISCOGS_RELEASE_ID')
        discogs_url = meta.get('discogs_url')

        if not discogs_id and not discogs_url:
            print("No Discogs ID or URL found in meta.")
            return

        # Extract Discogs ID from URL if no ID is present
        if not discogs_id and discogs_url:
            master_match = re.search(r"discogs\.com/master/(\d+)", discogs_url)
            if master_match:
                master_id = master_match.group(1)
                master_data = await dc.fetch_master_data(master_id)
                if master_data:
                    main_release_url = master_data.get("main_release_url", "")
                    discogs_id_match = re.search(r"releases/(\d+)", main_release_url)
                    if discogs_id_match:
                        discogs_id = discogs_id_match.group(1)
                        meta['discogs_id'] = discogs_id
                        log.debug(f"Extracted Discogs ID from master URL: {discogs_id}")
                    else:
                        log.error("Failed to extract Discogs ID from master release URL.")
                else:
                    log.error(f"Failed to fetch master data for master ID {master_id}")

        if discogs_id:
            release_data = await dc.fetch_discogs_release(discogs_id)
            if release_data:
                barcode = dc.extract_barcode(release_data)
                if barcode and not meta.get('mbid'):
                    meta['barcode'] = barcode
                    print(f"Extracted Barcode: {barcode}")
                    # Retry MusicBrainz search if there's no MBID
                    await mb.search_musicbrainz_by_barcode(barcode, meta)

                dc.extract_metadata(release_data, meta)
        else:
            log.error("No valid Discogs ID found or could not extract it from the URL.")


    def get_music_source(self, normalized_af):
        nafu = normalized_af.upper()
        if ' CD ' in nafu:
            return 'CD' 
        elif ' VINYL ' in nafu:
            return 'VINYL'    
        elif ' WEB ' in nafu:
            return 'WEB'
        else:
            return ''


    """
    Get and parse mediainfo
    """
    async def exportInfo(self, media_path, isdir, folder_id, base_dir, export_text=True, dvd_patch=False):
        media_path = os.path.normpath(media_path)
        tmp_dir = Path(base_dir) / 'tmp' / folder_id
        tmp_dir.mkdir(parents=True, exist_ok=True)
        basename = os.path.basename(str(media_path))

        try:
            # Text output
            if export_text and (dvd_patch or not (tmp_dir / "MEDIAINFO.txt").exists()):
                console.print("[bold yellow]Exporting MediaInfo...")
                os.chdir(os.path.dirname(media_path))
                media_info = MediaInfo.parse(media_path, output="STRING", full=False, mediainfo_options={'inform_version': '1'})
                clean_path = media_info.replace(str(tmp_dir), folder_id) if dvd_patch else media_info.replace(media_path, basename)
                async with aiofiles.open(tmp_dir / "MEDIAINFO.txt", 'w', newline="", encoding='utf-8') as export:
                    await export.write(clean_path)
                console.print("[bold green]MediaInfo text Exported.") if not dvd_patch else console.print("[dim]DVD[/dim] [bold green]MediaInfo Patched.")

            # JSON output
            if not (tmp_dir / "MediaInfo.json").exists() or dvd_patch: #TODO alter LDU name logic for DVD's. MediaInfo.txt should suffice
                media_info = MediaInfo.parse(media_path, output="JSON", mediainfo_options={'inform_version': '1'})
                async with aiofiles.open(tmp_dir / "MediaInfo.json", 'w', encoding='utf-8') as export:
                    await export.write(media_info)
                console.print("[bold green]MediaInfo JSON Exported.")

            async with aiofiles.open(tmp_dir / "MediaInfo.json", 'r', encoding='utf-8') as f:
                return json.loads(await f.read())

        except FileNotFoundError:
            console.print(f"[bold red]File not found: {media_path}")
            sys.exit()
        except Exception as e:
            log.error(f"[bold red]Error processing MediaInfo for {media_path}: {e}")
            raise


    """
    Get Resolution
    """

    def get_resolution(self, guess, folder_id, base_dir):
        with open(f'{base_dir}/tmp/{folder_id}/MediaInfo.json', 'r', encoding='utf-8') as f:
            mi = json.load(f)
            try:
                width = mi['media']['track'][1]['Width']
                height = mi['media']['track'][1]['Height']
            except (KeyError, IndexError):
                width = 0
                height = 0
            scan = mi['media']['track'][1].get('ScanType', 'Progressive')       
            scan = "p" if scan == "Progressive" else "i"
            width_list = [3840, 2560, 1920, 1280, 1024, 854, 720, 15360, 7680, 0]
            height_list = [2160, 1440, 1080, 720, 576, 540, 480, 8640, 4320, 0]
            width = self.closest(width_list, int(width))
            actual_height = int(height)
            height = self.closest(height_list, int(height))
            res = f"{width}x{height}{scan}"
            resolution = self.mi_resolution(res, guess, width, scan, height, actual_height)
        return resolution

    def closest(self, lst, K):
        # Get closest, but not over
        lst = sorted(lst)
        mi_input = K
        res = 0
        for each in lst:
            if mi_input > each:
                pass
            else:
                res = each
                break
        return res

    def mi_resolution(self, res, guess, width, scan, height, actual_height):
        RESOLUTION_MAP = {
            "15360x8640p": "8640p",
            "7680x4320p": "4320p",
            "3840x2160p": "2160p",
            "2560x1440p": "1440p",
            "1920x1080p": "1080p",
            "1920x1080i": "1080i",
            "1280x720p": "720p",
            "1280x540p": "720p",
            "1280x576p": "720p",
            "1024x576p": "576p",
            "1024x576i": "576i",
            "720x576p": "576p",
            "720x576i": "576i",
            "854x480p": "480p",
            "854x480i": "480i",
            "720x480p": "480p",
            "720x480i": "480i",
            "OTHER": "OTHER"
        }

        WIDTH_FALLBACK = {
            15360: "8640p",
            7680: "4320p",
            3840: "2160p",
            2560: "1440p",
            1920: {"p": "1080p", "i": "1080i"},
            1280: "720p",
            1024: {"p": "576p", "i": "576i"},
            854: {"p": "480p", "i": "480i"},
            720: {"p": "576p", "i": "576i"}
        }

        if actual_height == 540:
            return "OTHER"

        resolution = RESOLUTION_MAP.get(res)
        if resolution:
            return resolution

        if res in RESOLUTION_MAP.values():
            return res

        try:
            resolution = guess['screen_size']
            if resolution in RESOLUTION_MAP.values():
                return resolution
        except (KeyError, TypeError):
            pass

        try:
            width = int(width)
        except (ValueError, TypeError):
            width = None

        if width:
            width_mapping = WIDTH_FALLBACK.get(width, "OTHER")
            if isinstance(width_mapping, dict):
                return width_mapping.get(scan, "OTHER")
            return width_mapping

        return "OTHER"
     
    def is_sd(self, resolution):
        if resolution in ("480i", "480p", "576i", "576p", "540p"):
            sd = 1
        else:
            sd = 0
        return sd

    """
    Is a sports release?
    """
    def is_sports(self, name):
        sports_pattern = r'(?:EFL.*|.*mlb.*|.*formula1.*|.*nascar.*|.*nfl.*|.*wrc.*|.*wwe.*|' \
                        r'.*fifa.*|.*boxing.*|.*rally.*|.*ufc.*|.*ppv.*|.*uefa.*|.*nhl.*|' \
                        r'.*nba.*|.*motogp.*|.*moto2.*|.*moto3.*|.*gamenight.*|.*darksport.*|' \
                        r'.*overtake.*)'
        return re.search(sports_pattern, name, re.IGNORECASE) is not None



    """
    Get type and category
    """

    def get_type(self, video, scene, is_disc):
        filename = os.path.basename(video).lower()
        if "remux" in filename:
            return "REMUX"
        elif any(word in filename for word in [" web ", ".web.", "web-dl", "webdl"]):
            return "WEBDL"
        elif "webrip" in filename:
            return "WEBRIP"
        elif "hdtv" in filename:
            return "HDTV"
        elif is_disc is not None:
            return "DISC"
        else:
            return "ENCODE"

    def get_cat(self, video):
        category = guessit(video.replace('1.0', ''))['type']
        if category.lower() == "movie":
            category = "MOVIE" #1
        elif category.lower() in ("tv", "episode"):
            category = "TV" #2
        else:
            category = "MOVIE"
        return category

    """
    Mediainfo/Bdinfo > meta
    """
    def get_audio_v2(self, mi, meta, bdinfo):
        if meta.get('is_music', False):  
            if not mi or 'media' not in mi or 'track' not in mi.get('media', {}):
                return "Unknown", "Unknown", False

            audio_tracks = mi['media'].get('track', [])
            if not audio_tracks:
                return "No Audio", "Unknown", False

            track = audio_tracks[0]
            format = track.get('Format', 'Unknown')
            channels = track.get('Channels', 'Unknown')
            if channels.isdigit():
                channels = f"{channels}.0"

            if track.get('Language', '') == "zxx":
                meta['silent'] = True

            # Simple codec mapping for music
            codec_map = {
                "DTS": "DTS",
                "AAC": "AAC",
                "AC-3": "DD",
                "E-AC-3": "DD+",
                "MLP FBA": "TrueHD",
                "FLAC": "FLAC",
                "Opus": "Opus",
                "Vorbis": "Vorbis",
                "PCM": "LPCM",
            }
            codec = codec_map.get(format, format)

            return f"{codec} {channels}", channels, False
        else:
            extra = dual = ""
            has_commentary = False
            #Get formats
            if bdinfo is not None: #Disks
                format_settings = ""
                format = bdinfo['audio'][0]['codec']
                commercial = format
                try:
                    additional = bdinfo['audio'][0]['atmos_why_you_be_like_this']
                except KeyError:
                    additional = ""
                #Channels
                chan = bdinfo['audio'][0]['channels']


            else: 
                track_num = 2
                for i in range(len(mi['media']['track'])):
                    t = mi['media']['track'][i]
                    if t['@type'] != "Audio":
                        pass
                    else: 
                        if t.get('Language', "") == meta.get('original_language') and "commentary" not in t.get('Title', '').lower():
                            track_num = i
                            break
                format = mi['media']['track'][track_num]['Format']
                commercial = mi['media']['track'][track_num].get('Format_Commercial', '')
                if mi['media']['track'][track_num].get('Language', '') == "zxx":
                    meta['silent'] = True
                try:
                    additional = mi['media']['track'][track_num]['Format_AdditionalFeatures']
                    # format = f"{format} {additional}"
                except KeyError:
                    additional = ""
                try:
                    format_settings = mi['media']['track'][track_num]['Format_Settings']
                    if format_settings in ['Explicit']:
                        format_settings = ""
                except KeyError:
                    format_settings = ""
                # Channels
                channels = mi['media']['track'][track_num].get('Channels_Original', mi['media']['track'][track_num]['Channels'])
                if not str(channels).isnumeric():
                    channels = mi['media']['track'][track_num]['Channels']

                channel_layout = mi['media']['track'][track_num].get('ChannelLayout', mi['media']['track'][track_num].get('ChannelLayout_Original', ''))

                if "LFE" in channel_layout:
                    chan = f"{int(channels) - 1}.1"
                elif not channel_layout:
                    chan = f"{int(channels)}.0" if int(channels) <= 2 else f"{int(channels) - 1}.1"
                else:
                    chan = f"{channels}.0"
                
                if meta.get('original_language') != 'en':
                    eng, orig = False, False
                    try:
                        for t in mi['media']['track']:
                            if t['@type'] != "Audio":
                                pass
                            else: 
                                audio_language = t.get('Language', '')
                                # Check for English Language Track
                                if audio_language == "en" and "commentary" not in t.get('Title', '').lower():
                                    eng = True
                                # Check for original Language Track
                                if audio_language == meta.get('original_language') and "commentary" not in t.get('Title', '').lower():
                                    orig = True
                                # Catch Chinese / Norwegian variants
                                variants = ['zh', 'cn', 'cmn', 'no', 'nb']
                                if audio_language in variants and meta.get('original_language') in variants:
                                    orig = True
                                # Check for additional, bloated Tracks
                                if audio_language != meta.get('original_language') and audio_language != "en":
                                    if meta['original_language'] not in variants and audio_language not in variants:
                                        audio_language = "und" if audio_language == "" else audio_language
                                        console.print(f"[bold red]This release has a(n) {audio_language} audio track, and may be considered bloated")
                                        time.sleep(5)
                        if eng and orig:
                            dual = "Dual-Audio"
                        elif eng and not orig and meta.get('original_language') not in ['zxx', 'xx', None] and not meta.get('no_dub', False):
                            dual = "Dubbed"
                    except Exception:
                        console.print(traceback.print_exc())
                        pass
                         
                for t in mi['media']['track']:
                    if t['@type'] != "Audio":
                        pass
                    else: 
                        if "commentary" in t.get('Title', '').lower():
                            has_commentary = True
            
            
            #Convert commercial name to naming conventions
            audio = {
                #Format
                "DTS": "DTS",
                "AAC": "AAC",
                "AAC LC": "AAC",
                "AC-3": "DD",
                "E-AC-3": "DD+",
                "MLP FBA": "TrueHD",
                "FLAC": "FLAC",
                "Opus": "Opus",
                "Vorbis": "Vorbis",
                "PCM": "LPCM",

                #BDINFO AUDIOS
                "LPCM Audio" : "LPCM",
                "Dolby Digital Audio" : "DD",
                "Dolby Digital Plus Audio" : "DD+",
                # "Dolby TrueHD" : "TrueHD",
                "Dolby TrueHD Audio" : "TrueHD",
                "DTS Audio" : "DTS", 
                "DTS-HD Master Audio" : "DTS-HD MA",
                "DTS-HD High-Res Audio" : "DTS-HD HRA",
                "DTS:X Master Audio" : "DTS:X"
            }
            audio_extra = {
                "XLL": "-HD MA",
                "XLL X": ":X",
                "ES": "-ES",
            }
            format_extra = {
                "JOC": " Atmos",
                "16-ch": " Atmos",
                "Atmos Audio": " Atmos",
            }
            format_settings_extra = {
                "Dolby Surround EX" : "EX"
            }

            commercial_names = {
                "Dolby Digital" : "DD",
                "Dolby Digital Plus" : "DD+",
                "Dolby TrueHD" : "TrueHD",
                "DTS-ES" : "DTS-ES",
                "DTS-HD High" : "DTS-HD HRA",
                "Free Lossless Audio Codec" : "FLAC",
                "DTS-HD Master Audio" : "DTS-HD MA"
                }

            
            search_format = True
            for key, value in commercial_names.items():
                if key in commercial:
                    codec = value
                    search_format = False
                if "Atmos" in commercial or format_extra.get(additional, "") == " Atmos":
                    extra = " Atmos"
            if search_format:
                codec = audio.get(format, "") + audio_extra.get(additional, "")
                extra = format_extra.get(additional, "")
            format_settings = format_settings_extra.get(format_settings, "")
            if format_settings == "EX" and chan == "5.1":
                format_settings = "EX"
            else:
                format_settings = ""

            if codec == "":
                codec = format
            
            if format.startswith("DTS"):
                if additional.endswith("X"):
                    codec = "DTS:X"
                    chan = f"{int(channels) - 1}.1"
            if format == "MPEG Audio":
                codec = mi['media']['track'][2].get('CodecID_Hint', '')

            

            audio = f"{dual} {codec} {format_settings} {chan}{extra}"
            audio = ' '.join(audio.split())
            return audio, chan, has_commentary


    def is_3d(self, mi, bdinfo):
        if bdinfo is not None:
            if bdinfo['video'][0]['3d'] != "":
                return "3D"
            return ""

    def get_tag(self, video, meta):
        try:
            tag = guessit(video)['release_group']
            tag = f"-{tag}"
        except Exception:
            tag = ""
        
        # Adjust to only keep the last part after the last dash
        if tag.startswith("-"):
            parts = tag[1:].split('-')
            tag = f"-{parts[-1]}"  # Keep only the last part

        if tag == "-":
            tag = ""
        if tag[1:].lower() in ["nogroup", 'nogrp']:
            tag = ""
        return tag


    def get_source(self, type: str, video: str, path: str, is_disc: Optional[str], meta: dict):
        try:
            source = meta.get('manual_source')
            if not source:
                try:
                    source = guessit(video).get('source', guessit(path).get('source', "BluRay"))
                except Exception:
                    source = "BluRay"
            if source in ("Blu-ray", "Ultra HD Blu-ray", "BluRay", "BR") or is_disc == "BDMV":
                source = "Blu-ray" if type == "DISC" else "BluRay"

            elif source in ("DVD", "dvd") or is_disc == "DVD":
                system = ""
                try:
                    if is_disc == "DVD":
                        mediainfo = MediaInfo.parse(f"{meta['discs'][0]['path']}/VTS_{meta['discs'][0]['main_set'][0][:2]}_0.IFO")
                    else:
                        mediainfo = MediaInfo.parse(video)
                    for track in mediainfo.tracks:
                        if track.track_type == "Video":
                            system = track.standard or ""
                            break
                except Exception:
                    try:
                        other = guessit(video).get('other', [])
                        if "PAL" in other:
                            system = "PAL"
                        elif "NTSC" in other:
                            system = "NTSC"
                    except Exception:
                        system = ""
                source = f"{system} DVD".strip() if type == "REMUX" else system
        
            elif source in ("Web", "WEB"):
                if type == "ENCODE":
                    type = "WEBRIP"           

            elif source in ("HD-DVD", "HD DVD", "HDDVD"):
                source = "HD DVD" if is_disc == "HDDVD" else "HDDVD"

            if type in ("WEBDL", "WEBRIP"):
                source = "Web"               
            if source == "Ultra HDTV":
                source = "UHDTV"
        except Exception as e:
            print(f"Error in get_source: {str(e)}")
            source = "BluRay"

        return source, type

    def get_uhd(self, type: str, guess: dict, resolution: str, path: str) -> str:
        try:
            source = guess.get('source', '')
            other = guess.get('other', '')
            
            if (source == 'Blu-ray' and other == "Ultra HD") or source == "Ultra HD Blu-ray":
                return "UHD"
            if "UHD" in path:
                return "UHD"
            if type in ("DISC", "REMUX", "ENCODE") and resolution == "2160p":
                return "UHD"
            return ""
        except Exception as e:
            console.print(f"Error in get_uhd: {str(e)}")
            return ""

    def get_hdr(self, mi: dict, bdinfo: Optional[dict]) -> str:
        try:
            hdr = ""
            dv = ""
            
            if bdinfo: #BDMV
                hdr_info = bdinfo['video'][0]['hdr_dv']
                hdr = "HDR10+" if "HDR10+" in hdr_info else "HDR" if hdr_info == "HDR10" else ""
                dv = "DV" if len(bdinfo['video']) > 1 and bdinfo['video'][1].get('hdr_dv') == "Dolby Vision" else ""
            else:  # File-based media
                video_track = mi['media']['track'][1]
                try:
                    if video_track.get('colour_primaries') in ("BT.2020", "REC.2020"):
                        hdr_format = video_track.get('HDR_Format_Compatibility', 
                                                video_track.get('HDR_Format_String', 
                                                                video_track.get('HDR_Format', "")))
                        if "HDR10+" in hdr_format:
                            hdr = "HDR10+"
                        elif "HDR10" in hdr_format:
                            hdr = "HDR"
                        elif not hdr_format and "PQ" in (video_track.get('transfer_characteristics'), 
                                                    video_track.get('transfer_characteristics_Original')):
                            hdr = "PQ10"
                        elif "HLG" in video_track.get('transfer_characteristics_Original', ''):
                            hdr = "HLG"
                        elif "BT.2020 (10-bit)" in video_track.get('transfer_characteristics_Original', ''):
                            hdr = "WCG"
                except Exception:
                    pass
                    
                dv = "DV" if "Dolby Vision" in video_track.get('HDR_Format', '') or \
                            "Dolby Vision" in video_track.get('HDR_Format_String', '') else ""
                
            return f"{dv} {hdr}".strip()
        except Exception as e:
            console.print(f"Error in get_hdr: {str(e)}")
            return ""

    def get_region(self, bdinfo, region=None):
            label = bdinfo.get('label', bdinfo.get('title', bdinfo.get('path', ''))).replace('.', ' ')
            if region is not None:
                region = region.upper()
            else:
                for key, value in self.regions.items(): 
                    if f" {key} " in label:
                        region = value
                        break
            return region if region is not None else ""

    def get_distributor(self, distributor_in):
            if distributor_in in [None, "None", ""]:
                return ""
            distributor_upper = distributor_in.upper()
            return distributor_upper if distributor_upper in self.distributor_list else ""

    def get_video_codec(self, bdinfo):
        codecs = {
            "MPEG-2 Video" : "MPEG-2",
            "MPEG-4 AVC Video" : "AVC",
            "MPEG-H HEVC Video" : "HEVC",
            "VC-1 Video" : "VC-1"
        }
        codec = codecs.get(bdinfo['video'][0]['codec'], "")
        return codec

    def get_video_encode(self, mi, type, bdinfo):
        video_encode = ""
        codec = ""
        bit_depth = '0'
        has_encode_settings = False
        try:
            format = mi['media']['track'][1]['Format']
            format_profile = mi['media']['track'][1].get('Format_Profile', format)
            if mi['media']['track'][1].get('Encoded_Library_Settings', None):
                has_encode_settings = True
            bit_depth = mi['media']['track'][1].get('BitDepth', '0')
        except KeyError:
            format = bdinfo['video'][0]['codec']
            format_profile = bdinfo['video'][0]['profile']

        # Determine codec based on type and format
        if type in ("ENCODE", "WEBRIP"):  # ENCODE or WEBRIP
            if format == 'AVC':
                codec = 'x264'
            elif format == 'HEVC':
                codec = 'x265'
        elif type in ('WEBDL', 'HDTV'):  # WEB-DL or HDTV
            if format == 'AVC':
                codec = 'H.264'
            elif format == 'HEVC':
                codec = 'H.265'
            if type == 'HDTV' and has_encode_settings:
                codec = codec.replace('H.', 'x')
        elif format == "VP9":
            codec = "VP9"
        elif format == "VC-1":
            codec = "VC-1"
        elif format == "AV1":
            codec = "AV1"

        # Determine profile
        if format_profile == 'High 10':
            profile = "Hi10P"
        else:
            profile = ""

        # Format video_encode based on codec and profile
        if profile:
            if codec:
                if profile != codec:
                    video_encode = f"{profile} {codec}"
                else:
                    video_encode = codec
            else:
                video_encode = profile
        else:
            if codec:
                video_encode = codec
            else:
                video_encode = format

        # Handle special case for MPEG Video
        video_codec = format
        if video_codec == "MPEG Video":
            video_codec = f"MPEG-{mi['media']['track'][1].get('Format_Version')}"

        return video_encode, video_codec, has_encode_settings, bit_depth

    def get_edition(self, title, video, bdinfo, filelist, manual_edition):
        video = video.lower().startswith('dc') and video.replace('dc', '', 1) or video
        guess = guessit(video)

        repack = ""
        cut = ""
        ratio = ""
        edition = ""

        if manual_edition is not None:
            edition = " ".join(manual_edition) if isinstance(manual_edition, list) else str(manual_edition)
            if any(x in edition.upper() for x in ["AI", "UPSCALE"]):
                edition = "AI UPSCALE"
        elif bdinfo:
            edition = guessit(bdinfo['label']).get('edition', "")
        else:
            edition = guess.get('edition', "")
        edition = " ".join(edition) if isinstance(edition, list) else edition

        if len(filelist) == 1:
            video = os.path.basename(video)
        video_normalized = re.sub(r'[.\-_!]+', ' ', video).strip()
        if title:
            title_normalized = re.sub(r'[.\-_!:]+', ' ', title.lower()).strip()
            title_pattern = re.escape(title_normalized).replace(' ', r'\s*')
            video_normalized = re.sub(rf"^{title_pattern}\s*", "", video_normalized.lower(), 1)
        if tag := guess.get('release_group'): 
            video_normalized = video_normalized.replace(tag.lower(), '')

        CUTS = {
            r"\bdirector'?s?\s*cut\b": "Director's Cut",
            r"\binternational\s*cut\b": "International Cut",
            r"\bextended\b": "Extended",
            r"\bspecial\s*edition\b": "Special Edition",
            r"\bunrated\b": "Unrated",
            r"\buncut\b": "Uncut",
            r"\bsuper\s*duper\b": "Super Duper Cut"
        }
        cut = next((value for pattern, value in CUTS.items() if re.search(pattern, video_normalized)),"")

        RATIOS = {
            r"\bimax\b": "IMAX",
            r"\bopen\s*matte\b": "Open Matte",
            r"\bopen\s*om\b": "Open Matte",
            r"\bmar\b": "MAR"
        }
        ratio = next((value for pattern, value in RATIOS.items() if re.search(pattern, video_normalized)),"")

        REPACK_PATTERNS = [
            (r"\brepack3\b|\[repack3\]|v4", "REPACK3"),
            (r"\brepack2\b|\[repack2\]|v3", "REPACK2"),
            (r"\brepack\b|\[repack\]|v2", "REPACK"),
            (r"\bproper\b", "PROPER"),
            (r"\brerip\b", "RERIP")
        ]
        for pattern, repack_type in REPACK_PATTERNS:
            if re.search(pattern, video_normalized):
                repack = repack_type
                break

        if re.search(r"\bhybrid\b", video_normalized) and not re.search(r"\bhybrid\b", title.lower()):
            edition = f"Hybrid {edition}"
        
        edition = re.sub(r"(repack\d?|rerip|proper)", "", edition, flags=re.IGNORECASE).strip()
        if edition.lower() in ['internal', 'limited', 'retail']:
            edition = ""

        return edition, repack, cut, ratio


    async def get_season_episode(self, video: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        filelist = meta['filelist']
        meta.update({'tv_pack': 0, 'episode_title_storage': '', 'episode_title' : ''})
        
        if not meta.get('anime', False):
            # Regular TV processing
            try:
                if meta.get('manual_date'):
                    guess_date = meta['manual_date']
                else:
                    guess = guessit(video)
                    guess_date = guess.get('date')
                    season_int = int(guess.get('season', 1))
                    season = f"S{str(season_int).zfill(2)}"
                    if season_int == int(guess.get('year', 0)) and f"s{season_int}" not in video.lower():
                        season_int, season = 1, "S01"
                    
                    if len(filelist) == 1:
                        episodes = guess.get('episode', 0)
                        if isinstance(episodes, list):
                            episode = ''.join(f"E{str(ep).zfill(2)}" for ep in episodes)
                            episode_int = int(episodes[0])
                        else:
                            episode_int = int(episodes)
                            episode = f"E{str(episode_int).zfill(2)}"
                    else:
                        episode, episode_int = "", 0
                        meta['tv_pack'] = 1
                        
                if guess_date:
                    season_int, episode_int = self.daily_to_tmdb_season_episode(meta.get('tmdb'), guess_date)
                    season, episode = str(guess_date), ""
                    
            except Exception:
                console.print_exception()
                season, season_int = "S01", 1
                episode, episode_int = "", 0
                meta['tv_pack'] = 1
                
        else:
            # Anime processing
            parsed = anitopy.parse(Path(video).name)
            romaji, mal_id, _, season_year, anilist_episodes = await self.TMDb.get_romaji(parsed['anime_title'], meta.get('mal'))
            
            if mal_id:
                meta['mal_id'] = mal_id
                
            if not meta.get('tmdb_manual'):
                year = parsed.get('anime_year', str(season_year))
                meta = await self.TMDb.get_tmdb_id(parsed['anime_title'], year, meta, 'TV')
            
            meta = await self.TMDb.tmdb_other_meta(meta)
            if meta['category'] != 'TV':
                return meta
                
            if tag := parsed.get('release_group'):
                meta['tag'] = f"-{tag}"
                
            # Episode handling
            if len(filelist) == 1:
                try:
                    episodes = parsed.get('episode_number', guessit(video).get('episode', 1))
                    if isinstance(episodes, list):
                        episode = ''.join(f"E{str(ep).zfill(2)}" for ep in episodes)
                        episode_int = int(episodes[0])
                    else:
                        episode_int = int(episodes)
                        episode = f"E{str(episode_int).zfill(2)}"
                except Exception:
                    episode, episode_int = "E01", 1
                    console.print('[bold yellow]There was an error guessing the episode number. Guessing E01. Use [bold green]--episode #[/bold green] to correct if needed')
                    await asyncio.sleep(1.5)
            else:
                episode, episode_int = "", 0
                meta['tv_pack'] = 1
                
            # Season handling
            try:
                season_int = int(meta.get('season_int', parsed.get('anime_season', guessit(video)['season'])))
                season = f"S{str(season_int).zfill(2)}"
            except Exception:
                try:
                    if episode_int >= anilist_episodes:
                        response = requests.post("https://thexem.info/map/single", params={
                            'id': str(meta['tvdb_id']),
                            'origin': 'tvdb',
                            'absolute': str(episode_int)
                        }).json()
                        if response['result'] != "failure":
                            season_int = int(response['data']['scene']['season'])
                            season = f"S{str(season_int).zfill(2)}"
                            if len(filelist) == 1:
                                episode_int = int(response['data']['scene']['episode'])
                                episode = f"E{str(episode_int).zfill(2)}"
                        else:
                            raise Exception
                    else:
                        season, season_int = "S01", 1
                except Exception:
                    try:
                        season_int = int(guessit(video)['season'])
                        season = f"S{str(season_int).zfill(2)}"
                    except Exception:
                        season, season_int = "S01", 1
                    console.print(f"[bold yellow]{meta['title']} does not exist on thexem, guessing {season}")
                    console.print(f"[bold yellow]If [green]{season}[/green] is incorrect, use --season to correct")
                    await asyncio.sleep(3)

        if not meta.get('manual_season'):
            meta['season'] = season
        else:
            season_int = int(meta['manual_season'].lower().replace('s', ''))
            meta['season'] = f"S{str(season_int).zfill(2)}"
            
        if meta.get('manual_episode'):
            episode_int = int(meta['manual_episode'].lower().replace('e', ''))
            meta.update({'episode': f"E{str(episode_int).zfill(2)}", 'tv_pack': 0})
        else:
            meta['episode'] = episode
            
        meta['episode_title_storage'] = guessit(video, {"excludes": "part"}).get('episode_title', '')
        if meta['season'] == "S00" or meta['episode'] == "E00":
            meta['episode_title'] = meta['episode_title_storage']
            
        if meta['tv_pack'] == 1:
            part = guessit(os.path.dirname(video)).get('part')
            meta['part'] = f"Part {part}" if part else ""
        else:
            meta['part'] = ""
            
        meta['season_int'] = season_int
        meta['episode_int'] = episode_int
        return meta


    def get_service(self, video, tag, audio, guess_title):
        service = guessit(video).get('streaming_service', "")
        video_name = re.sub("[.()]", " ", video.replace(tag, '').replace(guess_title, ''))
        video_name = video_name.replace("DTS-HD.MA.", "").replace("DTS-HD MA ", "")
        
        # Iterate over service codes and their alias lists
        for service_code, aliases in self.services.items():
            for key in aliases: 
                if (' ' + key + ' ') in video_name and key not in guessit(video, {"excludes": ["country", "language"]}).get('title', ''):
                    service = service_code
                elif key == service:
                    service = service_code
        
        # Find the longest alias for the service code
        service_longname = service
        for service_code, aliases in self.services.items():
            if service_code is service and aliases:  
                service_longname = max(aliases, key=len, default=service_longname)
        
        if service_longname == "Amazon Prime":
            service_longname = "Amazon"
        return service, service_longname

    def stream_optimized(self, stream_opt):
        if stream_opt is True:
            stream = 1
        else:
            stream = 0
        return stream

    def is_anon(self, anon_in):
        anon = self.config['DEFAULT'].get("Anon", "False")
        if anon.lower() == "true":
            console.print("[bold red]Global ANON has been removed in favor of per-tracker settings. Please update your config accordingly.")
            time.sleep(10)
        if anon_in is True:
            anon_out = 1
        else:
            anon_out = 0
        return anon_out        
    
    def clean_filename(self, name):
        invalid = r'<>:"/\|?*'
        for char in invalid:
            name = name.replace(char, '-')
        return name

    async def gen_desc(self, meta):
        desclink = meta.get('desclink', None)
        auto_desc = meta.get('auto_desc', False)
        nfo_file = meta.get('nfo_file', None)
        description_path = f"{meta['base_dir']}/tmp/{meta['uuid']}/DESCRIPTION.txt"
        descfile_content = ""
        custom_desc_written = False

        with open(description_path, 'w', newline="", encoding='utf8') as description:
            if (desclink, meta.get('descfile', None), meta['desc']) == (None, None, None):
                desc_source = []
                if meta.get('ptp_manual') is not None:
                    desc_source.append('PTP')
                if meta.get('blu_manual') is not None:
                    desc_source.append('BLU')
                desc_source = desc_source[0] if len(desc_source) == 1 else None

                if meta.get('ptp') and str(self.config['TRACKERS'].get('PTP', {}).get('useAPI')).lower() == "true" and desc_source in ['PTP', None]:
                    ptp = PTP(config=self.config)
                    ptp_desc = await ptp.get_ptp_description(meta['ptp'], meta['is_disc'])
                    if ptp_desc.strip():
                        description.write(ptp_desc)
                        description.write("\n")
                        meta['description'] = 'PTP'
                        custom_desc_written = True

                if not custom_desc_written and meta.get('blu_desc', '').strip() and desc_source in ['BLU', None]:
                    blu_desc = meta['blu_desc'].strip()
                    if blu_desc:
                        description.write(blu_desc)
                        meta['description'] = 'BLU'
                        custom_desc_written = True

            if meta.get('desc_template'):
                from jinja2 import Template
                with open(f"{meta['base_dir']}/data/templates/{meta['desc_template']}.txt", 'r') as f:
                    desc_template = Template(f.read())
                    template_desc = desc_template.render(meta)
                    if template_desc.strip():
                        description.write(template_desc)
                        description.write("\n")
                        custom_desc_written = True

            if meta['nfo']:
                with open(nfo_file, 'r', encoding="utf-8") as nfo_file:
                    nfo_content = nfo_file.read()
                description.write("\n" + "[code]")
                description.write(nfo_content)
                description.write("[/code]" + "\n")
                meta['description'] = "CUSTOM"
                custom_desc_written = True

            if desclink:
                parsed = urllib.parse.urlparse(desclink.replace('/raw/', '/'))
                split = os.path.split(parsed.path)
                raw = parsed._replace(path=f"{split[0]}/raw/{split[1]}") if split[0] != '/' else parsed._replace(path=f"/raw{parsed.path}")
                raw = urllib.parse.urlunparse(raw)
                description.write(requests.get(raw).text)
                description.write("\n")
                meta['description'] = "CUSTOM"
                custom_desc_written = True

            if auto_desc:
                path = Path(meta.get('path', ''))
                uuid = meta.get('uuid', '')
                if uuid:
                    uuid = Path(uuid).stem
                desc_folder = self.config['AUTO']['description_folder']
                descfile = None

                if desc_folder:
                    desc_folder = Path(desc_folder)
                    if desc_folder.is_dir():
                        potential_file = desc_folder / f"{uuid}.txt"
                        if potential_file.is_file():
                            descfile = potential_file

                if descfile is None and path.exists():
                    if path.is_file():
                        descfile = path.with_suffix('.txt')
                        if not descfile.is_file():
                            descfile = path.parent / f"{uuid}.txt"
                    elif path.is_dir():
                        descfile = path / f"{uuid}.txt"
                        if not descfile.is_file():
                            descfile = path / 'description.txt'

                if descfile and descfile.is_file():
                    with descfile.open('r') as f:
                        descfile_content = f.read()
                        description.write("\n" + descfile_content + "\n")
                        meta['description'] = "CUSTOM"
                        custom_desc_written = True

                desc = meta.get('desc')
                if desc and desc != descfile_content:
                    description.write("\n" + desc + "\n")
                    meta['description'] = "CUSTOM"
                    custom_desc_written = True

            if meta.get('descfile') and os.path.isfile(meta['descfile']):
                with open(meta['descfile'], 'r') as f:
                    descfile_text = f.read()
                    description.write("\n" + descfile_text + "\n")
                meta['description'] = "CUSTOM"
                custom_desc_written = True

            if meta.get('desc'):
                description.write("\n" + meta['desc'] + "\n")
                meta['description'] = "CUSTOM"
                custom_desc_written = True

        return meta

    
    async def tag_override(self, meta):
        with open(f"{meta['base_dir']}/data/tags.json", 'r', encoding="utf-8") as f:
            tags = json.load(f)
            f.close()
        
        for tag in tags:
            value = tags.get(tag)
            if value.get('in_name', "") == tag and tag in meta['path']:
                meta['tag'] = f"-{tag}"
            if meta['tag'][1:] == tag:
                for key in value:
                    if key == 'type':
                        if meta[key] == "ENCODE":
                            meta[key] = value.get(key)
                        else:
                            pass
                    elif key == 'personalrelease':
                        meta[key] = bool(value.get(key, False))
                    elif key == 'template':
                        meta['desc_template'] = value.get(key)
                    else:
                        meta[key] = value.get(key)
        return meta

    async def get_dvd_size(self, discs):
        sizes = []
        dvd_sizes = []
        for each in discs:
            sizes.append(each['size'])
        grouped_sizes = [list(i) for j, i in itertools.groupby(sorted(sizes))]
        for each in grouped_sizes:
            if len(each) > 1:
                dvd_sizes.append(f"{len(each)}x{each[0]}")
            else:
                dvd_sizes.append(each[0])
        dvd_sizes.sort()
        compact = " ".join(dvd_sizes)
        return compact
   