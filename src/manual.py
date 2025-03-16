import os
import re
import glob
import json
import shutil
import urllib.parse
import requests
from torf import Torrent
from src.screenshots import ScreenshotUploader
from rich.console import Console

console = Console()

class ManualPackager:
    def __init__(self, config, img_host, base_dir):
        self.config = config
        self.base_dir = base_dir
        self.uploader = ScreenshotUploader(config, img_host, base_dir)

    async def package(self, meta):
        """Packages metadata and files for manual upload."""
        if meta['tag'] == "":
            tag = ""
        else:
            tag = f" / {meta['tag'][1:]}"
        if meta['is_disc'] == "DVD":
            res = meta['source']
        else:
            res = meta['resolution']

        with open(f"{meta['base_dir']}/tmp/{meta['uuid']}/GENERIC_INFO.txt", 'w', encoding="utf-8") as generic:
            generic.write(f"Name: {meta['name']}\n\n")
            generic.write(f"Overview: {meta['overview']}\n\n")
            generic.write(f"{res} / {meta['type']}{tag}\n\n")
            generic.write(f"Category: {meta['category']}\n")
            generic.write(f"TMDB: https://www.themoviedb.org/{meta['category'].lower()}/{meta['tmdb']}\n")
            if meta['imdb_id'] != "0":
                generic.write(f"IMDb: https://www.imdb.com/title/tt{meta['imdb_id']}\n")
            if meta['tvdb_id'] != "0":
                generic.write(f"TVDB: https://www.thetvdb.com/?id={meta['tvdb_id']}&tab=series\n")
            poster_img = f"{meta['base_dir']}/tmp/{meta['uuid']}/POSTER.png"
            if meta.get('poster', None) not in ['', None] and not os.path.exists(poster_img):
                if meta.get('rehosted_poster', None) is None:
                    r = requests.get(meta['poster'], stream=True)
                    if r.status_code == 200:
                        console.print("[bold yellow]Rehosting Poster")
                        r.raw.decode_content = True
                        with open(poster_img, 'wb') as f:
                            shutil.copyfileobj(r.raw, f)
                        poster_list, _ = await self.uploader.upload_screenshots(meta, 1, custom_img_list=[poster_img], total_screens=1)
                        if poster_list:
                            poster = poster_list[0]
                            generic.write(f"TMDB Poster: {poster.get('raw_url', poster.get('img_url'))}\n")
                            meta['rehosted_poster'] = poster.get('raw_url', poster.get('img_url'))
                            with open(f"{meta['base_dir']}/tmp/{meta['uuid']}/meta.json", 'w', encoding='utf-8') as metafile:
                                json.dump(meta, metafile, indent=4)
                        else:
                            console.print("[bold yellow]Failed to rehost poster")
                    else:
                        console.print("[bold yellow]Poster could not be retrieved")
            elif os.path.exists(poster_img) and meta.get('rehosted_poster') is not None:
                generic.write(f"TMDB Poster: {meta.get('rehosted_poster')}\n")
            if len(meta['image_list']) > 0:
                generic.write(f"\nImage Webpage:\n")
                for each in meta['image_list']:
                    generic.write(f"{each['web_url']}\n")
                generic.write(f"\nThumbnail Image:\n")
                for each in meta['image_list']:
                    generic.write(f"{each['img_url']}\n")
        
        title = re.sub(r"[^0-9a-zA-Z\[\]]+", "", meta['title'])
        archive = f"{meta['base_dir']}/tmp/{meta['uuid']}/{title}"
        torrent_files = glob.glob1(f"{meta['base_dir']}/tmp/{meta['uuid']}", "*.torrent")
        if isinstance(torrent_files, list) and len(torrent_files) > 1:
            for each in torrent_files:
                if not each.startswith(('BASE', '[RAND')):
                    os.remove(os.path.abspath(f"{meta['base_dir']}/tmp/{meta['uuid']}/{each}"))
        
        try:
            if os.path.exists(f"{meta['base_dir']}/tmp/{meta['uuid']}/BASE.torrent"):
                base_torrent = Torrent.read(f"{meta['base_dir']}/tmp/{meta['uuid']}/BASE.torrent")
                manual_name = re.sub(r"[^0-9a-zA-Z\[\]\'\-]+", ".", os.path.basename(meta['path']))
                Torrent.copy(base_torrent).write(f"{meta['base_dir']}/tmp/{meta['uuid']}/{manual_name}.torrent", overwrite=True)
            filebrowser = self.config['TRACKERS'].get('MANUAL', {}).get('filebrowser', None)
            shutil.make_archive(archive, 'tar', f"{meta['base_dir']}/tmp/{meta['uuid']}")
            if filebrowser is not None:
                url = '/'.join(s.strip('/') for s in (filebrowser, f"/tmp/{meta['uuid']}"))
                url = urllib.parse.quote(url, safe="https://")
            else:
                files = {"files[]": (f"{meta['title']}.tar", open(f"{archive}.tar", 'rb'))}
                response = requests.post("https://uguu.se/upload.php", files=files).json()
                if meta['debug']:
                    console.print(f"[cyan]{response}")
                url = response['files'][0]['url']
            return url
        except Exception:
            return False