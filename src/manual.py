import glob
import json
import re
import shutil
from pathlib import Path
from typing import Dict

import aiofiles
import httpx
import urllib.parse
from torf import Torrent

from src.console import console, log
from src.screenshots import ScreenshotUploader


class ManualPackager:
    def __init__(self, config, img_host, base_dir):
        self.config = config
        self.base_dir = Path(base_dir)
        self.uploader = ScreenshotUploader(config, img_host, base_dir)

    async def package(self, meta: Dict) -> str | bool:
        """Packages metadata and files for manual upload asynchronously."""
        tag = f" / {meta['tag'][1:]}" if meta['tag'] else ""
        res = meta['source'] if meta['is_disc'] == "DVD" else meta['resolution']

        generic_path = self.base_dir / "tmp" / meta['uuid'] / "GENERIC_INFO.txt"
        async with aiofiles.open(generic_path, 'w', encoding="utf-8") as generic:
            await generic.write(f"Name: {meta['name']}\n\n")
            await generic.write(f"Overview: {meta['overview']}\n\n")
            await generic.write(f"{res} / {meta['type']}{tag}\n\n")
            await generic.write(f"Category: {meta['category']}\n")
            await generic.write(f"TMDB: https://www.themoviedb.org/{meta['category'].lower()}/{meta['tmdb']}\n")
            if meta['imdb_id'] != "0":
                await generic.write(f"IMDb: https://www.imdb.com/title/tt{meta['imdb_id']}\n")
            if meta['tvdb_id'] != "0":
                await generic.write(f"TVDB: https://www.thetvdb.com/?id={meta['tvdb_id']}&tab=series\n")

        poster_img = self.base_dir / "tmp" / meta['uuid'] / "POSTER.png"
        if meta.get('poster') and not poster_img.exists():
            if meta.get('rehosted_poster') is None:
                async with httpx.AsyncClient() as client:
                    try:
                        response = await client.get(meta['poster'], timeout=10)
                        response.raise_for_status()
                        log.info("Rehosting Poster")
                        async with aiofiles.open(poster_img, 'wb') as f:
                            await f.write(response.content)
                        poster_list, _ = await self.uploader.upload_screenshots(
                            meta, 1, custom_img_list=[str(poster_img)], total_screens=1
                        )
                        if poster_list:
                            poster = poster_list[0]
                            async with aiofiles.open(generic_path, 'a', encoding="utf-8") as generic:
                                await generic.write(f"TMDB Poster: {poster.get('raw_url', poster.get('img_url'))}\n")
                            meta['rehosted_poster'] = poster.get('raw_url', poster.get('img_url'))
                            meta_path = self.base_dir / "tmp" / meta['uuid'] / "meta.json"
                            async with aiofiles.open(meta_path, 'w', encoding='utf-8') as metafile:
                                await metafile.write(json.dumps(meta, indent=4))
                        else:
                            log.warning("Failed to rehost poster")
                    except httpx.RequestError as e:
                        log.warning(f"Poster could not be retrieved: {e}")
        elif poster_img.exists() and meta.get('rehosted_poster'):
            async with aiofiles.open(generic_path, 'a', encoding="utf-8") as generic:
                await generic.write(f"TMDB Poster: {meta.get('rehosted_poster')}\n")

        if meta['image_list']:
            async with aiofiles.open(generic_path, 'a', encoding="utf-8") as generic:
                await generic.write("\nImage Webpage:\n")
                for each in meta['image_list']:
                    await generic.write(f"{each['web_url']}\n")
                await generic.write("\nThumbnail Image:\n")
                for each in meta['image_list']:
                    await generic.write(f"{each['img_url']}\n")

        title = re.sub(r"[^0-9a-zA-Z\[\]]+", "", meta['title'])
        archive = self.base_dir / "tmp" / meta['uuid'] / title
        torrent_dir = self.base_dir / "tmp" / meta['uuid']
        torrent_files = glob.glob1(str(torrent_dir), "*.torrent")
        
        if isinstance(torrent_files, list) and len(torrent_files) > 1:
            for each in torrent_files:
                if not each.startswith(('BASE', '[RAND')):
                    (torrent_dir / each).unlink()

        try:
            base_torrent_path = torrent_dir / "BASE.torrent"
            if base_torrent_path.exists():
                base_torrent = Torrent.read(str(base_torrent_path))
                manual_name = re.sub(r"[^0-9a-zA-Z\[\]\'\-]+", ".", Path(meta['path']).name)
                manual_torrent_path = torrent_dir / f"{manual_name}.torrent"
                Torrent.copy(base_torrent).write(str(manual_torrent_path), overwrite=True)

            filebrowser = self.config['TRACKERS'].get('MANUAL', {}).get('filebrowser')
            shutil.make_archive(str(archive), 'tar', str(torrent_dir))
            
            if filebrowser:
                url = '/'.join(s.strip('/') for s in (filebrowser, f"/tmp/{meta['uuid']}"))
                url = urllib.parse.quote(url, safe="https://")
            else:
                async with httpx.AsyncClient() as client:
                    async with aiofiles.open(f"{archive}.tar", 'rb') as tar_file:
                        files = {"files[]": (f"{meta['title']}.tar", await tar_file.read(), 'application/x-tar')}
                        response = await client.post("https://uguu.se/upload.php", files=files)
                        response.raise_for_status()
                        response_json = response.json()
                        if meta['debug']:
                            console.print(f"[cyan]{response_json}")
                        url = response_json['files'][0]['url']
            return url
        except (httpx.RequestError, OSError) as e:
            log.error(f"Failed to package or upload archive: {e}")
            return False