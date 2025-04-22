import asyncio
import json
import itertools
import os
import re
import time
import urllib
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import anitopy
import requests
from guessit import guessit
from pymediainfo import MediaInfo
from tinytag import TinyTag

from src.args import Args
from src.console import console, log
from src.databases import AniListHandler, DiscogsAPI, IMDbHandler, MusicBrainzAPI, TMDbHandler, is_scene, search_tvmaze
from src.discparse import DiscParse
from src.mediainfo import MediaInfoHandler
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
                                             
    def __init__(self, config: Dict, base_dir: Path):
        self.config = config
        self.base_dir = base_dir
        self._regions = None
        self._distributor_list = None
        self._services = None
        self.TMDb = TMDbHandler(config)
        self.IMDb = IMDbHandler()
        self.mihandle = MediaInfoHandler(base_dir)

    async def gather_prep(self, meta, mode):
        #Initialize gather
        meta.update(
            mode=mode,
            is_dir=Path(meta['path']).is_dir(),
            is_music=False,
            is_video=False,
            filelist=[],
            user_images=[],
            log_files=[],
            nfo_file='',
        )

        # Create temp directory
        tmp_dir = self.base_dir / 'tmp' / f"{meta['uuid']}"
        await asyncio.to_thread(tmp_dir.mkdir, parents=True, exist_ok=True)

        log.debug(f"[cyan]ID: {meta['uuid']}")

        # File collection and initial processing
        if meta['is_dir']:
            meta = await self._process_directory(meta, mode)
        else:
            meta = await self._process_single_file(meta, mode)

        if meta['is_music']:
            console.print('[red]Processing as music')
            meta = await self.gen_desc(meta)
            return meta

        # Video processing
        console.print('[red]Processing as video')
        meta = await self._process_video_metadata(meta)

        meta = await self.gen_desc(meta)
        return meta


    async def _process_directory(self, meta: Dict, mode: str) -> Dict:
        """Process directory contents recursively with video priority."""
        path = Path(meta['path'])
        video_extensions = {'.mkv', '.mp4', '.ts'}
        music_extensions = {'.aac', '.ac3', '.alac', '.eac3', '.flac', '.m4a', '.mp3', '.ogg', '.ogm', '.oga', '.ogv', '.ogx', '.opus', '.spx', '.wav'}
        image_extensions = {'.jpg', '.jpeg', '.png', '.svg', '.ico', '.gif'}

        all_files = []
        total_size = 0
        for file_path in sorted(path.rglob('*'), key=str):
            if file_path.is_file() and not file_path.name.startswith('._'):
                all_files.append(str(file_path))
                total_size += file_path.stat().st_size

        meta['content_size'] = total_size
        meta['filelist'] = all_files

        # Categorize files
        video_files = [f for f in all_files if Path(f).suffix.lower() in video_extensions]
        music_files = [f for f in all_files if Path(f).suffix.lower() in music_extensions]
        meta['user_images'] = [f for f in all_files if Path(f).suffix.lower() in image_extensions]
        meta['log_files'] = [f for f in all_files if Path(f).suffix.lower() == '.log']
        nfo_files = [f for f in all_files if Path(f).suffix.lower() == '.nfo']

        base_name = path.name
        for nfo in nfo_files:
            if Path(nfo).name == f"{base_name}.nfo":
                meta['nfo_file'] = nfo
                break
        else:
            meta['nfo_file'] = nfo_files[0] if nfo_files else ''

        # Prioritize video if in base directory or dominant
        base_video = any(Path(f).parent == path for f in video_files)
        video_count = len(video_files)
        music_count = len(music_files)
        log.debug(f"Found {video_count} video files, {music_count} music files")

        if video_count > 0 and (base_video or video_count > music_count * 2 or not meta.get('is_music')):
            meta['is_video'] = True
            meta['video_files'] = video_files  # Store for get_video
            console.print(f"[yellow]Video files: {[Path(f).name for f in video_files]}")
        elif music_count > 0:
            meta['is_music'] = True
            meta['music_files'] = music_files
            console.print('[red]Processing as music')
            await self.get_music(meta, mode)

        return meta

    async def _process_single_file(self, meta: Dict, mode: str) -> Dict:
        """Process a single file with video priority and calculate size."""
        file_path = Path(meta['path'])
        file_extension = file_path.suffix.lower()

        try:
            meta['content_size'] = file_path.stat().st_size
        except (OSError, FileNotFoundError) as e:
            log.error(f"Error getting file size: {e}")
            meta['content_size'] = 0

        meta['filelist'] = [str(file_path)]

        if file_extension in {'.mkv', '.mp4', '.ts'}:
            meta['is_video'] = True
            meta['video_files'] = [str(file_path)]
            base_name = file_path.stem
            nfo_path = file_path.with_name(f"{base_name}.nfo")
            if nfo_path.exists():
                meta['nfo_file'] = str(nfo_path)
            elif meta.get('nfo'):
                nfo_files = [f for f in file_path.parent.iterdir() if f.suffix.lower() == '.nfo']
                if nfo_files:
                    meta['nfo_file'] = str(nfo_files[0])
        elif file_extension in {'.aac', '.alac', '.flac', '.m4a', '.mp3', '.opus', '.wav'} and meta.get('is_music'):
            meta['is_music'] = True
            meta['music_files'] = [file_path]
            console.print('[red]Processing as music')
            await self.process_single_music_file(meta, mode)

        return meta

    async def _process_video_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Orchestrate video metadata processing."""
        # Initial setup
        meta['is_disc'], videoloc, bdinfo, meta['discs'] = await self.get_disc(meta)
        meta.update({
            'bdinfo': bdinfo,
            'video': None,
            'is_sports': False,
            'mediainfo': None
        })

        # Process disc type and set initial metadata
        meta = await self._process_disc_type(meta, videoloc)

        # Tracker API integrations
        await self._integrate_tracker_apis(meta)

        # Finalize category-specific metadata
        meta = await self._finalize_metadata(meta)

        return meta

    async def _process_disc_type(self, meta: Dict[str, Any], videoloc: str) -> Dict[str, Any]:
        """Handle video processing based on disc type and set initial metadata."""
        guess = None
        if meta['is_disc'] == "BDMV":
            video, meta['scene'], meta['imdb'] = await is_scene(meta['path'], meta.get('imdb'))
            meta['video'] = video
            try:
                title = meta['discs'][0]['bdinfo']['title']
                guess_name = re.sub(r'Blu-ray™|-\s*|Vol\.\s*\d+', '', title).strip()
                filename = guessit(re.sub(r"[^0-9a-zA-Z\[\]]+", " ", guess_name), {"excludes": ["country", "language"]})['title']
                meta['search_year'] = guessit(meta['bdinfo']['title']).get('year', "")
            except Exception:
                guess_name = meta['bdinfo']['label'].replace('-', ' ')
                filename = guessit(re.sub(r"[^0-9a-zA-Z\[\]]+", " ", guess_name), {"excludes": ["country", "language"]})['title']
                meta['search_year'] = guessit(meta['bdinfo']['label']).get('year', "")
            meta['resolution'] = self.mihandle._map_resolution(
                meta['bdinfo']['video'][0]['res'], guessit(video), "OTHER", "p", "OTHER", 0
            )
            meta['filename'] = filename.split(' AKA ')[0] if " AKA " in filename.replace('.', ' ') else filename
            meta['type'] = self.get_type(video, meta['scene'], meta['is_disc'])
            meta['category'] = meta.get('category') or self.get_cat(meta['video'], meta['is_disc'], meta.get('discs', [])) or guess.get('type', 'MOVIE').upper()

        elif meta['is_disc'] == "DVD":
            disc_path = meta['discs'][0]['path']
            video, meta['scene'], meta['imdb'] = await is_scene(meta['path'], meta.get('imdb'))
            guess_name = disc_path.replace('-', ' ')
            meta['filename'] = guessit(guess_name, {"excludes": ["country", "language"]})['title']
            meta['search_year'] = guessit(video).get('year', "")
            meta['video'] = video
            if not meta.get('edit', False):
                title_set = meta['discs'][0]['main_set'][0][:2]
                vob_path = f"{disc_path}/VTS_{title_set}_1.VOB"
                meta['mediainfo'] = await self.mihandle.exportInfo(
                    media_path=vob_path,
                    is_dir=False,
                    folder_id=meta['uuid'],
                    export_text=True
                )
            meta['dvd_size'] = await self.get_dvd_size(meta['discs'])
            meta['type'] = self.get_type(video, meta['scene'], meta['is_disc'])
            meta['category'] = meta.get('category') or self.get_cat(meta['video'], meta['is_disc'], meta.get('discs', [])) or guess.get('type', 'MOVIE').upper()

        elif meta['is_disc'] == "HDDVD":
            video, meta['scene'], meta['imdb'] = await is_scene(meta['path'], meta.get('imdb'))
            guess_name = meta['discs'][0]['path'].replace('-', '')
            meta['filename'] = guessit(guess_name, {"excludes": ["country", "language"]})['title']
            meta['search_year'] = guessit(meta['discs'][0]['path']).get('year', "")
            meta['video'] = video
            if not meta.get('edit', False):
                meta['mediainfo'] = await self.mihandle.exportInfo(
                    media_path=video,
                    is_dir=False,
                    folder_id=meta['uuid'],
                    export_text=False
                )
            meta['type'] = self.get_type(video, meta['scene'], meta['is_disc'])
            meta['category'] = meta.get('category') or self.get_cat(meta['video'], meta['is_disc'], meta.get('discs', [])) or guess.get('type', 'MOVIE').upper()

        else:  # Non-disc case
            videopath, filelist = self.get_video(videoloc, meta.get('mode', 'discord'), meta.get('filelist', []))
            video, meta['scene'], meta['imdb'] = await is_scene(videopath, meta.get('imdb'))
            meta['video'] = str(videopath)
            guess = guess or guessit(meta['video'])
            meta['category'] = meta.get('category') or self.get_cat(meta['video'], meta['is_disc'], meta.get('discs', [])) or guess.get('type', 'MOVIE').upper()
            if meta['category'] == 'TV':
                guess_name = meta['uuid'].replace('-', " ") 
            else:    
                guess_name = Path(video).name.replace('-', " ")
            meta['filename'] = guessit(re.sub(r"[^0-9a-zA-Z\[\]]+", " ", guess_name), {"excludes": ["country", "language"]}).get(
                "title", guessit(re.sub("[^0-9a-zA-Z]+", " ", guess_name), {"excludes": ["country", "language"]})["title"]
            )
            meta['search_year'] = guessit(video).get('year', "")
            if not meta.get('edit', False):
                meta['mediainfo'] = await self.mihandle.exportInfo(
                    media_path=videopath,
                    is_dir=meta.get('is_dir', False),
                    folder_id=meta['uuid'],
                    export_text=True
                )
            meta['type'] = self.get_type(video, meta['scene'], meta['is_disc'])
        if self.is_sports(meta['uuid']):
            meta['is_sports'] = True
            meta['category'] = 'SPORTS'
        return meta

    async def _assign_common_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Assign common metadata attributes"""
        async def fetch_resolution():
            return self.mihandle.get_resolution(guessit(meta['video']), meta['uuid'])

        async def fetch_audio():
            return self.mihandle.get_audio(meta.get('mediainfo'), meta, meta.get('bdinfo'))

        async def fetch_3d():
            return self.is_3d(meta.get('mediainfo'), meta.get('bdinfo'))

        async def fetch_source():
            return self.get_source(meta['type'], meta['video'], meta['path'], meta['is_disc'], meta)

        async def fetch_uhd():
            return self.get_uhd(meta['type'], guessit(meta['path']), meta['resolution'], meta['path'])

        async def fetch_hdr():
            return self.get_hdr(meta.get('mediainfo'), meta.get('bdinfo'))

        async def fetch_video_encode():
            if meta['is_disc'] != "BDMV":
                return self.mihandle.get_video_encode(meta.get('mediainfo'), meta['type'], meta.get('bdinfo'))
            return None, None, False, '0'

        results = await asyncio.gather(
            fetch_resolution(),
            fetch_audio(),
            fetch_3d(),
            fetch_source(),
            fetch_uhd(),
            fetch_hdr(),
            fetch_video_encode()
        )

        meta['resolution'] = meta.get('resolution') or results[0]
        meta['sd'] = 1 if meta['resolution'] in ("480i", "480p", "576i", "576p", "540p") else 0
        meta['audio'], meta['channels'], meta['has_commentary'], meta['is_silent'] = results[1]
        meta['3D'] = results[2]
        meta['source'], meta['type'] = results[3]
        meta['uhd'] = results[4]
        meta['hdr'] = results[5]

        if meta['is_disc'] == "BDMV":
            meta['region'] = self.get_region(meta.get('bdinfo'), meta.get('region'))
            meta['video_codec'] = self.midhandle.get_video_codec(meta['bdinfo'])
        else:
            meta['video_encode'], meta['video_codec'], meta['has_encode_settings'], meta['bit_depth'] = results[6]

        return meta

    async def _finalize_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """Finalize metadata based on category and additional processing."""
        if meta.get('search_year') in ('', None):
            meta['search_year'] = await self.mihandle.get_search_year(meta.get('mediainfo', {}), meta)
        if meta['category'] in ("TV", "MOVIE"):
            meta['tmdb'] = meta.get('tmdb_manual', meta.get('tmdb'))
            if not meta.get('imdb') and meta.get('imdb_id'):
                meta['imdb'] = f"tt{meta['imdb_id']}"
            if not meta.get('tmdb') and not meta.get('imdb'):
                meta['category'], meta['tmdb'], meta['imdb'] = self.get_tmdb_imdb_from_mediainfo(
                    meta.get('mediainfo'), meta['category'], meta['is_disc'], meta['tmdb'], meta['imdb']
                )
            if not meta.get('tmdb') and not meta.get('imdb'):
                meta = await self.TMDb.get_tmdb_id(meta['filename'], meta['search_year'], meta, meta['category'], meta['filename'])
            elif meta.get('imdb') and not meta.get('tmdb_manual'):
                meta['imdb_id'] = str(meta['imdb']).replace('tt', '')
                meta = await self.TMDb.get_tmdb_from_imdb(meta, meta['filename'])
            else:
                meta['tmdb_manual'] = meta.get('tmdb')

            if meta.get('unattended') and (meta.get('tmdb') is None or int(meta['tmdb']) == 0):
                meta['tmdb_not_found'] = True
                console.print(f"[red]Unable to find TMDb match for {Path(meta['uuid']).stem}")
                return meta

            meta = await (self.IMDb.imdb_other_meta(meta) if int(meta['tmdb']) == 0 else self.TMDb.tmdb_other_meta(meta))
            meta['tvmaze_id'], meta['imdb_id'], meta['tvdb_id'] = await search_tvmaze(
                meta['filename'], meta['search_year'], meta.get('imdb_id', '0'), meta.get('tvdb_id', 0)
            )
            if not meta.get('imdb_id'):
                meta['imdb_id'] = await self.IMDb.search_imdb(meta['filename'], meta['search_year'])
            if not meta.get('imdb_info') and int(meta['imdb_id']) != 0:
                meta['imdb_info'] = await self.IMDb.get_imdb_info(meta['imdb_id'], meta)

        meta['tag'] = meta.get('tag') or self.get_tag(meta['video'], meta)
        if meta['tag'] and not meta['tag'].startswith('-'):
            meta['tag'] = f"-{meta['tag']}"
        if meta['category'] == 'TV':
            meta = await self.get_season_episode(meta['video'], meta)

        # Assign common metadata 
        meta = await self._assign_common_metadata(meta) #After TMDb for Dual/Dub/Silent

        # Additional cleanup
        meta = await self.tag_override(meta)
        if meta['tag'][1:].startswith(meta['channels']):
            meta['tag'] = meta['tag'].replace(f"-{meta['channels']}", '')
        if meta.get('no_tag', False):
            meta['tag'] = ""
        if meta['type'] in ('WEBDL', 'WEBRIP', 'HDTV'):
            meta['service'], meta['service_longname'] = self.get_service(
                meta['video'], meta.get('tag', ''), meta['audio'], meta['filename']
            ) if meta.get('service') in (None, '') else (meta['service'], meta['service_longname'])

        meta['distributor'] = self.get_distributor(meta['distributor'])
        meta['edition'], meta['repack'], meta['cut'], meta['ratio'] = self.get_edition(
            meta.get('title', ''), meta['path'], meta.get('bdinfo'), meta['filelist'], meta.get('manual_edition')
        )
        if "REPACK" in meta.get('edition', ""):
            meta['repack'] = re.search(r"REPACK[\d]?", meta['edition'])[0]
            meta['edition'] = re.sub(r"REPACK[\d]?", "", meta['edition']).strip().replace('  ', ' ')

        meta['stream'] = self.stream_optimized(meta.get('stream', False))
        meta['anon'] = self.is_anon(meta.get('anon', False))

        if meta['category'] == 'SPORTS':
            meta['name'] = meta['title'] = Path(meta['video']).stem if meta['scene'] else Path(meta['uuid']).stem

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
            filepath = self.base_dir/ "data"/ f"{filename}"
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
                discs, bdinfo = await parse.get_bdinfo(discs, meta['uuid'], self.base_dir, meta.get('discs', []))
            else:
                discs, bdinfo = await parse.get_bdinfo(meta['discs'], meta['uuid'], self.base_dir, meta['discs'])
        elif is_disc == "DVD":
            discs = await parse.get_dvdinfo(discs)
            export = open(self.base_dir / 'tmp'/ f"{meta['uuid']}/MEDIAINFO.txt", 'w', newline="", encoding='utf-8')
            export.write(discs[0]['ifo_mi'])
            export.close()
        elif is_disc == "HDDVD":
            discs = await parse.get_hddvd_info(discs)
            export = open(self.base_dir / 'tmp'/ f"{meta['uuid']}/MEDIAINFO.txt", 'w', newline="", encoding='utf-8')
            export.write(discs[0]['evo_mi'])
            export.close()
        discs = sorted(discs, key=lambda d: d['name'])
        return is_disc, videoloc, bdinfo, discs



    """
    Get video files

    """
    def get_video(self, videoloc: Union[str, Path], mode: str, meta_or_filelist: Union[Dict, List[str]] = None) -> Tuple[Union[Path, None], List[Path]]:
        """Retrieve the primary video file and list of video files from meta or filelist."""
        video_extensions = {'.mkv', '.mp4', '.ts'}
        bonus_keywords = ('bonus', 'extra', 'bloopers', 'blooper', 'intro', 'trailer', 'sample', '0')

        def is_bonus_file(filename: Union[str, Path]) -> bool:
            name = Path(filename).stem.lower()
            return any(name.startswith(k) or name.endswith(k) for k in bonus_keywords)

        # Handle meta (dict) or filelist (list)
        video_files = []
        if isinstance(meta_or_filelist, dict):
            video_files = [Path(f) for f in meta_or_filelist.get('video_files', [])]  # Convert strings to Path
        elif isinstance(meta_or_filelist, list):
            video_files = [Path(f) for f in meta_or_filelist if Path(f).suffix.lower() in video_extensions]  # Filter valid videos

        # Fallback to scanning videoloc if no video files
        if not video_files:
            videoloc = Path(videoloc).resolve()
            if videoloc.is_dir():
                video_files = [f for f in videoloc.iterdir() if f.is_file() and f.suffix.lower() in video_extensions and not f.name.startswith('._')]
            elif videoloc.suffix.lower() in video_extensions and not videoloc.name.startswith('._'):
                video_files = [videoloc]

        if not video_files:
            console.print(f"[red]No video files found in {videoloc}")
            if mode == 'cli':
                exit()
            return None, []

        # Sort to prioritize non-bonus files
        video_files = sorted(video_files, key=is_bonus_file)
        primary_video = video_files[0]
        return primary_video, video_files

    async def get_music(self, meta: Dict, mode: str) -> Dict:
        log.debug("Starting get_music")
        meta['is_music'] = True
        musicloc = Path(meta['path'])
        
        console.print(f"[yellow]Checking music directory: {musicloc}")

        # Sort tracks, covers, and proof
        tracks, cover, back_cover, proof = self.sort_music(meta, musicloc)
        meta.update(
            track_count=len(tracks),
            user_cover=str(cover) if cover else None,
            back_cover=str(back_cover) if back_cover else None,
            user_proof=str(proof) if proof else None,
        )

        if not self.has_sufficient_music_metadata(meta):
            await self.process_music(meta, self.base_dir, tracks)
            log.debug("Music processing completed")

        return meta

    """SORT MUSIC"""

    def sort_music(self, meta: Dict, musicloc: Path) -> Tuple[List[Tuple[str, str, Path]], Optional[Path], Optional[Path], Optional[Path]]:
        """Sort music files, detect covers, and identify proof images."""
        cover, back_cover, proof = None, None, None
        tracks = []
        music_extensions = {'.aac', '.ac3', '.alac', '.eac3', '.flac', '.m4a', '.mp3', '.ogg', '.ogm', '.oga', '.ogv', '.ogx', '.opus', '.spx', '.wav'}

        # Convert filelist strings to Path objects
        filelist = [Path(p) for p in meta['filelist']]

        # Detect disc folders
        has_disc_folders = any(
            any(folder_name.lower() in p.parent.name.lower() for folder_name in ['disc', 'cd', 'record'])
            for p in filelist if p.is_file()
        )
        log.debug(f"[blue]Has disc folders: {has_disc_folders}")

        for file_path in filelist:
            if not file_path.is_file() or file_path.name.startswith('._'):
                continue

            relative_path = file_path.relative_to(musicloc)
            file_name = file_path.name
            ext = file_path.suffix.lower()

            # Handle music tracks
            if ext in music_extensions and not (file_name.lower().endswith('sample.mp3') or file_name.lower().startswith('!sample')):
                # Determine disc number
                disc = "Disc 1"
                if has_disc_folders:
                    for part in relative_path.parts[:-1]:
                        if any(folder_name.lower() in part.lower() for folder_name in ['disc', 'cd', 'record']):
                            disc = part.title()
                            break

                # Extract track number
                track_match = re.match(r'([A-Z]|\d+)(?:[\s.-])?(.*)', file_name, re.IGNORECASE)
                if not track_match:
                    log.warning(f"[yellow]Could not extract track number from: {file_name}")
                    continue

                track_num = track_match.group(1)
                rest_of_file = track_match.group(2).strip()

                if len(track_num) == 3 and track_num.isdigit():
                    disc_num = track_num[0]
                    disc = f"Disc {int(disc_num)}"
                    track = track_num[1:]
                    file_name = f"{track}-{rest_of_file}"
                    log.debug(f"[yellow]Detected multi-disc album: {disc}, File: {file_name}")
                elif track_num.isdigit() and len(track_num) > 2:
                    file_name = re.sub(r'^\d{3}-', '01-', file_name)
                    log.debug(f"[purple]Adjusted file for multi-disc: {file_name}")

                tracks.append((disc, file_name, file_path))
                log.debug(f"[green]Added music file: {disc}, File: {file_name}")

        # Check for alphanumeric track numbers to set source
        has_alpha_tracks = any(
            re.match(r'^[A-Z]\d*$', t[1].split()[0], re.IGNORECASE)
            for t in tracks
        )
        if has_alpha_tracks:
            meta['source'] = 'VINYL'
            tracks = [(t[0].replace("Disc 1", "Record"), t[1], t[2]) for t in tracks]

        # Sort tracks
        def sort_key(track: Tuple[str, str, Path]) -> Tuple[int, int]:
            disc_num = re.search(r'\d+', track[0].split()[-1])
            disc_number = int(disc_num.group()) if disc_num else 1

            track_part = track[1].split()[0]
            match = re.match(r'^([A-Z])?(\d+)(?:[\s\.\-_\,](\d+))?', track_part, re.IGNORECASE)
            if match:
                letter, number, sub_number = match.groups()
                primary = (ord(letter.upper()) - ord('A') + 1) * 1000 if letter else 0
                secondary = int(number or '0') * 10
                tertiary = int(sub_number or '0')
                return (disc_number, primary + secondary + tertiary)
            elif track_part.isdigit():
                return (disc_number, int(track_part))
            return (disc_number, 999)

        tracks.sort(key=sort_key)
        log.debug(f"[blue]Sorted tracks: {tracks}")

        # Handle cover and proof images
        front_cover = ['front', 'cover', 'album', 'disc1', 'cd1']
        back_covers = ['back_cover', 'rear', 'backside', 'back']

        for img_path in meta['user_images']:
            img_path = Path(img_path)
            file_name = img_path.name.lower().replace(' ', '')
            ext = img_path.suffix.lower()

            if ext not in {'.jpg', '.jpeg'}:
                continue

            if 'proof' in file_name:
                proof = img_path
                log.debug(f"[magenta]Assigned proof image: {proof}")
                continue

            if any(variant in file_name for variant in back_covers):
                back_cover = img_path
                log.debug(f"[yellow]Assigned back cover image: {back_cover}")
                continue

            if cover is None and any(variant in file_name for variant in front_cover):
                cover = img_path
                log.debug(f"[cyan]Found cover image: {cover}")
                continue

            if cover is None and 'proof' not in file_name:
                cover = img_path
                log.debug(f"[cyan]Fallback cover image: {cover}")

        return tracks, cover, back_cover, proof

    async def process_single_music_file(self, meta: Dict, mode: str) -> Dict:
        """Process a single music file."""
        musicloc = Path(meta['path'])
        if musicloc.name.startswith('._'):
            return meta

        meta['track_count'] = 1
        parent_dir = musicloc.parent

        # Look for cover and proof images
        for entry in parent_dir.iterdir():
            if entry.is_file() and not entry.name.startswith('._'):
                if entry.name.lower() in ('cover.jpg', 'cover.jpeg', 'front.jpg', 'front.jpeg', 'album.jpg', 'album.jpeg'):
                    meta['user_cover'] = str(entry)
                elif entry.suffix.lower() in ('.jpg', '.jpeg') and 'proof' in entry.name.lower():
                    meta['user_proof'] = str(entry)

        if not self.has_sufficient_music_metadata(meta) or meta.get('mbid_manual'):
            tracks = [("Disc 1", musicloc.name, musicloc)]
            await self.process_music(meta, self.base_dir, tracks)

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
        mi = await self.mihandle.exportInfo(
                    media_path=first_audio_file,
                    is_dir=meta.get('is_dir', False),
                    folder_id=meta['uuid'],
                    export_text=True
                )
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
            self.export_audio_info(tracks, meta['uuid'], self.base_dir)

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

    def get_cat(self, video, is_disc, discs):
        if is_disc and len(discs) > 2:
            return "TV"
        category = guessit(video.replace('1.0', ''))['type']
        if category.lower() == "movie":
            return "MOVIE" #1
        elif category.lower() in ("tv", "episode"):
            return "TV" #2
        else:
            return "MOVIE"



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
            log.error(f"Error in get_hdr: {str(e)}")
            return ""


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



    def get_region(self, bdinfo, region=None):
            label = bdinfo.get('label', bdinfo.get('title', bdinfo.get('path', ''))).replace('.', ' ')
            if region is not None:
                region = region.upper()
            else:
                for key, value in self._regions.items(): 
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
            if not meta.get('mal_id'):
                romaji, mal_id, _, season_year, anilist_episodes = await AniListHandler().get_romaji(parsed['anime_title'], meta.get('mal'))
                if mal_id:
                    meta['mal_id'] = mal_id
                
            if not meta.get('tmdb_manual') and not meta.get('tmdb'):
                year = parsed.get('anime_year', str(season_year))
                meta = await self.TMDb.get_tmdb_id(parsed['anime_title'], year, meta, 'TV')
            
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
        description_path = self.base_dir / 'tmp'/ f"{meta['uuid']}/DESCRIPTION.txt"
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
                        if not any(line.strip().startswith("# USE:") for line in descfile_content.splitlines()):
                            descfile_content = "# USE: full\n" + descfile_content
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
        with open(self.base_dir / "data/tags.json", 'r', encoding="utf-8") as f:
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
   