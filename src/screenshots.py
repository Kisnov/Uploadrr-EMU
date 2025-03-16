import os
import glob
import random
import json
import time
import platform
import asyncio
import nest_asyncio
import base64
import requests
import subprocess
import traceback
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, TextColumn, BarColumn, TimeRemainingColumn
import ffmpeg
from pymediainfo import MediaInfo
import pyimgbox

console = Console()

class ScreenshotGenerator:
    def __init__(self, screens, img_host, config, base_dir):
        self.screens = screens
        self.img_host = img_host.lower()
        self.config = config
        self.base_dir = base_dir

    def disc_screenshots(self, filename, bdinfo, folder_id, use_vs, image_list, ffdebug, num_screens=None):
        if num_screens is None:
            num_screens = self.screens
        if num_screens == 0 or len(image_list) >= num_screens:
            return

        # Get longest m2ts
        length = 0
        file = None
        for each in bdinfo['files']:
            int_length = sum(int(float(x)) * 60 ** i for i, x in enumerate(reversed(each['length'].split(':'))))
            if int_length > length:
                length = int_length
                for root, dirs, files in os.walk(bdinfo['path']):
                    for name in files:
                        if name.lower() == each['file'].lower():
                            file = f"{root}/{name}"

        keyframe = 'nokey' if ("VC-1" in bdinfo['video'][0]['codec'] or bdinfo['video'][0]['hdr_dv'] != "") else 'none'

        os.chdir(f"{self.base_dir}/tmp/{folder_id}")
        i = len(glob.glob(f"{filename}-*.png"))
        
        if i >= num_screens:
            console.print('[bold green]Reusing screenshots')
            return

        console.print("[bold yellow]Saving Screens...")
        if use_vs:
            from src.vs import vs_screengn
            vs_screengn(source=file, encode=None, filter_b_frames=False, num=num_screens, dir=f"{self.base_dir}/tmp/{folder_id}/")
        else:
            self._generate_ffmpeg_screenshots(file, filename, folder_id, num_screens, length, keyframe, ffdebug)

    def dvd_screenshots(self, meta, disc_num, num_screens=None):
        if num_screens is None:
            num_screens = self.screens
        if num_screens == 0 or (len(meta.get('image_list', [])) >= num_screens and disc_num == 0):
            return

        ifo_mi = MediaInfo.parse(f"{meta['discs'][disc_num]['path']}/VTS_{meta['discs'][disc_num]['main_set'][0][:2]}_0.IFO", mediainfo_options={'inform_version': '1'})
        sar = 1
        width = height = length = par = dar = None
        for track in ifo_mi.tracks:
            if track.track_type == "Video":
                length = float(track.duration) / 1000
                par = float(track.pixel_aspect_ratio)
                dar = float(track.display_aspect_ratio)
                width = float(track.width)
                height = float(track.height)

        w_sar, h_sar = self._calculate_sar(par, dar, width, height)
        
        main_set = self._get_dvd_main_set(meta['discs'][disc_num]['main_set'])
        
        os.chdir(f"{self.base_dir}/tmp/{meta['uuid']}")
        existing_screenshots = glob.glob(f"{self.base_dir}/tmp/{meta['uuid']}/{meta['discs'][disc_num]['name']}-*.png")
        
        if len(existing_screenshots) >= num_screens:
            console.print('[bold green]Reusing screenshots')
            return
            
        self._generate_dvd_screenshots(meta, disc_num, num_screens, length, main_set, w_sar, h_sar, width, height)

    def screenshots(self, path, filename, folder_id, meta, num_screens=None):
        if num_screens is None:
            num_screens = self.screens - len(meta.get('image_list', []))
        if num_screens == 0:
            return

        with open(f"{self.base_dir}/tmp/{folder_id}/MediaInfo.json", encoding='utf-8') as f:
            mi = json.load(f)
            video_track = mi['media']['track'][1]
            length = float(video_track.get('Duration', mi['media']['track'][0]['Duration']))
            width = float(video_track.get('Width'))
            height = float(video_track.get('Height'))
            par = float(video_track.get('PixelAspectRatio', 1))
            dar = float(video_track.get('DisplayAspectRatio'))

        w_sar, h_sar = self._calculate_sar(par, dar, width, height)
        
        os.chdir(f"{self.base_dir}/tmp/{folder_id}")
        if len(glob.glob(f"{filename}-*.png")) >= num_screens:
            console.print('[bold green]Reusing screenshots')
            return

        if meta.get('vapoursynth', False):
            from src.vs import vs_screengn
            vs_screengn(source=path, encode=None, filter_b_frames=False, num=num_screens, dir=f"{self.base_dir}/tmp/{folder_id}/")
        else:
            self._generate_file_screenshots(path, filename, folder_id, num_screens, length, w_sar, h_sar, width, height, meta)

    def _generate_ffmpeg_screenshots(self, file, filename, folder_id, num_screens, length, keyframe, ffdebug):
        loglevel = 'verbose' if ffdebug else 'quiet'
        debug = not ffdebug
        
        with Progress(TextColumn("[bold green]Saving Screens..."), BarColumn(), "[cyan]{task.completed}/{task.total}", TimeRemainingColumn()) as progress:
            screen_task = progress.add_task("[green]Saving Screens...", total=num_screens)
            ss_times = []
            smallest_image_path = None
            smallest_image_size = float('inf')
            i = len(glob.glob(f"{filename}-*.png"))

            for _ in range(num_screens):
                image_path = f"{self.base_dir}/tmp/{folder_id}/{filename}-{i}.png"
                if not os.path.exists(image_path):
                    try:
                        ss_times = self.valid_ss_time(ss_times, num_screens, length)
                        ffmpeg.input(file, ss=ss_times[-1], skip_frame=keyframe).output(image_path, vframes=1, pix_fmt="rgb24").overwrite_output().global_args('-loglevel', loglevel).run(quiet=debug)
                        self.optimize_images(image_path)
                        if self._check_image_size(image_path):
                            i += 1
                    except Exception:
                        console.print(traceback.format_exc())
                else:
                    screenshot_size = os.path.getsize(image_path)
                    if screenshot_size < smallest_image_size:
                        smallest_image_size = screenshot_size
                        smallest_image_path = image_path
                progress.advance(screen_task)
                
            if smallest_image_path:
                os.remove(smallest_image_path)

    def _generate_dvd_screenshots(self, meta, disc_num, num_screens, length, main_set, w_sar, h_sar, width, height):
        loglevel = 'quiet'
        debug = True
        if meta.get('ffdebug', False):
            loglevel = 'verbose'
            debug = False
        
        with Progress(TextColumn("[bold green]Saving Screens..."), BarColumn(), "[cyan]{task.completed}/{task.total}", TimeRemainingColumn()) as progress:
            screen_task = progress.add_task("[green]Saving Screens...", total=num_screens)
            ss_times = []
            smallest_image_path = None
            smallest_image_size = float('inf')
            n = 0
            i = 0
            retake = False
            looped = 0

            for _ in range(num_screens):
                image_path = f"{self.base_dir}/tmp/{meta['uuid']}/{meta['discs'][disc_num]['name']}-{i}.png"
                if not os.path.exists(image_path) or retake:
                    try:
                        retake = False
                        if n >= len(main_set):
                            n = 0
                        if n >= num_screens:
                            n -= num_screens

                        voblength, n = self._is_vob_good(meta, disc_num, main_set, n, num_screens)
                        min_time = 0.01 * voblength
                        base_time = max(min_time, random.randint(round(voblength / 5), round(voblength - voblength / 5)))
                        while True:
                            img_time = max(min_time, base_time / (2 ** i) + random.uniform(0, 20))
                            if img_time < voblength:
                                break

                        ff = ffmpeg.input(f"{meta['discs'][disc_num]['path']}/VTS_{main_set[n]}", ss=img_time)
                        if w_sar != 1 or h_sar != 1:
                            ff = ff.filter('scale', int(round(width * w_sar)), int(round(height * h_sar)))
                        ff.output(image_path, vframes=1, pix_fmt="rgb24").overwrite_output().global_args('-loglevel', loglevel).run(quiet=debug)

                        self.optimize_images(image_path)
                        n += 1

                        if os.path.getsize(Path(image_path)) <= 75000:
                            console.print("[yellow]Image is incredibly small (and is most likely to be a single color), retaking")
                            retake = True
                            time.sleep(1)
                        elif os.path.getsize(Path(image_path)) <= 31000000 and self.img_host == "imgbb":
                            i += 1
                        elif os.path.getsize(Path(image_path)) <= 10000000 and self.img_host in ["imgbox", "pixhost", "ptscreens", "oeimg"]:
                            i += 1
                        elif self.img_host in ["ptpimg", "lensdump", "ptscreens", "oeimg"]:
                            i += 1
                        else:
                            console.print("[red]Image too large for your image host, retaking")
                            retake = True
                            time.sleep(1)
                        
                        looped = 0
                    except Exception:
                        console.print(traceback.format_exc())
                        if looped >= 25:
                            console.print('[red]Failed to take screenshots')
                            raise SystemExit
                        looped += 1
                else:
                    screenshot_size = os.path.getsize(image_path)
                    if screenshot_size < smallest_image_size:
                        smallest_image_size = screenshot_size
                        smallest_image_path = image_path
                
                i += 1
                progress.advance(screen_task)

            if smallest_image_path:
                os.remove(smallest_image_path)


    def _generate_file_screenshots(self, path, filename, folder_id, num_screens, length, w_sar, h_sar, width, height, meta):
        loglevel = 'quiet'
        debug = True
        if meta.get('ffdebug', False):
            loglevel = 'verbose'
            debug = False
        
        with Progress(TextColumn("[bold green]Saving Screens..."), BarColumn(), "[cyan]{task.completed}/{task.total}", TimeRemainingColumn()) as progress:
            screen_task = progress.add_task("[green]Saving Screens...", total=num_screens)
            ss_times = []
            smallest_image_path = None
            smallest_image_size = float('inf')
            i = 0
            retake = False

            for _ in range(num_screens):
                image_path = os.path.abspath(f"{self.base_dir}/tmp/{folder_id}/{filename}-{i}.png")
                if not os.path.exists(image_path) or retake:
                    try:
                        ss_times = self.valid_ss_time(ss_times, num_screens, length)
                        ff = ffmpeg.input(path, ss=ss_times[-1])
                        if w_sar != 1 or h_sar != 1:
                            ff = ff.filter('scale', int(round(width * w_sar)), int(round(height * h_sar)))
                        ff.output(image_path, vframes=1, pix_fmt="rgb24").overwrite_output().global_args('-loglevel', loglevel).run(quiet=debug)
                        
                        self.optimize_images(image_path)
                        size = os.path.getsize(Path(image_path))
                        if size <= 75000 or self.is_black_image(image_path):
                            console.print("[yellow]Image is incredibly small or black, retaking")
                            retake = True
                            os.remove(image_path)
                            time.sleep(1)
                        elif size <= 31000000 and self.img_host == "imgbb" and not retake:
                            i += 1
                        elif size <= 10000000 and self.img_host in ["imgbox", "pixhost", "ptscreens", "oeimg"] and not retake:
                            i += 1
                        elif self.img_host in ["ptpimg", "lensdump", "ptscreens", "oeimg"] and not retake:
                            i += 1
                        elif self.img_host == "freeimage.host":
                            console.print("[bold red]Support for freeimage.host has been removed. Please remove from your config")
                            raise SystemExit
                        else:
                            console.print("[red]Image too large for your image host, retaking")
                            retake = True
                            os.remove(image_path)
                            time.sleep(1)
                    except Exception:
                        console.print(traceback.format_exc())
                else:
                    screenshot_size = os.path.getsize(image_path)
                    if screenshot_size < smallest_image_size:
                        smallest_image_size = screenshot_size
                        smallest_image_path = image_path
                
                i += 1
                progress.advance(screen_task)
            
            if smallest_image_path:
                os.remove(smallest_image_path)

    def _calculate_sar(self, par, dar, width, height):
        if par == 1:
            return 1, 1
        elif par < 1:
            new_height = dar * height
            sar = width / new_height
            return 1, sar
        else:
            return par, 1

    def _check_image_size(self, image_path):
        size = os.path.getsize(Path(image_path))
        if size <= 75000:
            console.print("[bold yellow]Image is incredibly small, retaking")
            os.remove(image_path)
            time.sleep(1)
            return False
        elif size <= 31000000 and self.img_host == "imgbb":
            return True
        elif size <= 10000000 and self.img_host in ["imgbox", "pixhost", "ptscreens", "oeimg"]:
            return True
        elif self.img_host in ["ptpimg", "lensdump", "ptscreens", "oeimg"]:
            return True
        else:
            console.print("[red]Image too large for your image host, retaking")
            os.remove(image_path)
            time.sleep(1)
            return False

    def valid_ss_time(self, ss_times, num_screens, length, min_time_diff=10):
        valid_time = False
        while not valid_time:
            valid_time = True
            if ss_times:
                sst = random.randint(round(length / 5), round(length / 2))
                tolerance = length / 10 / num_screens
                for each in ss_times:
                    if abs(sst - each) <= tolerance or any(abs(sst - t) < min_time_diff for t in ss_times):
                        valid_time = False
                if valid_time:
                    ss_times.append(sst)
            else:
                ss_times.append(random.randint(round(length / 5), round(length / 2)))
        return ss_times

    def optimize_images(self, image):
        if self.config['DEFAULT'].get('optimize_images', True) and os.path.exists(image):
            try:
                pyver = platform.python_version_tuple()
                if int(pyver[0]) == 3 and int(pyver[1]) >= 7:
                    import oxipng
                    level = 6 if os.path.getsize(image) >= 31000000 else 1
                    oxipng.optimize(image, level=level)
            except:
                pass

    def is_black_image(self, image_path, threshold=0.98):
        try:
            command = ['ffmpeg', '-i', image_path, '-vf', f'blackdetect=d=0.1:pic_th={threshold}', '-f', 'null', '-']
            result = subprocess.run(command, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            return 'black_start' in result.stderr
        except Exception as e:
            console.print(f"[red]Error checking black image: {e}")
            return False

    def _get_dvd_main_set(self, main_set):
        main_set_length = len(main_set)
        if main_set_length >= 3:
            return main_set[1:-1]
        elif main_set_length == 2:
            return main_set[1:]
        return main_set

    def _is_vob_good(self, meta, disc_num, main_set, n, num_screens):
        voblength = 300
        loops = 0
        while loops < 6:
            vob_mi = MediaInfo.parse(f"{meta['discs'][disc_num]['path']}/VTS_{main_set[n]}", output='JSON')
            vob_mi = json.loads(vob_mi)
            try:
                voblength = float(vob_mi['media']['track'][1]['Duration'])
                return voblength, n
            except Exception:
                try:
                    voblength = float(vob_mi['media']['track'][2]['Duration'])
                    return voblength, n
                except Exception:
                    n += 1
                    if n >= len(main_set):
                        n = 0
                    if n >= num_screens:
                        n -= num_screens
                    loops += 1
        return 300, n
    

class ScreenshotUploader:
    def __init__(self, config, img_host, base_dir):
        self.config = config
        self.img_host = img_host.lower()
        self.base_dir = base_dir
        self.timeout = 60  # Default timeout for uploads

    async def upload_screenshots(self, meta, screens, img_host_num=1, custom_img_list=None, total_screens=None):
        """
        Upload screenshots to the specified image host.
        
        Args:
            meta: Metadata dictionary containing base_dir, uuid, etc.
            screens: Number of screenshots to upload
            img_host_num: Image host number to try (starts at 1)
            custom_img_list: Optional list of specific images to upload (for proofs/covers)
            total_screens: Total number of screenshots expected (defaults to screens if not provided)
        
        Returns:
            Tuple of (image_list, count of uploaded images)
        """
        total_screens = total_screens or screens
        custom_img_list = custom_img_list or []
        return_dict = {}

        if total_screens != 0 or len(meta.get('image_list', [])) > total_screens:
            if not custom_img_list:
                console.print('[yellow]Uploading Screens')

        os.chdir(f"{self.base_dir}/tmp/{meta['uuid']}")
        img_host = self.config['DEFAULT'].get(f'img_host_{img_host_num}', self.img_host)
        
        # Handle override from meta['imghost'] or fallback logic
        if img_host != self.img_host and meta.get('imghost') is None:
            img_host = self.img_host
        if img_host_num == 1 and meta.get('imghost') and meta['imghost'] != img_host:
            img_host = meta['imghost']
            img_host_num = 0

        image_list = []
        newhost_list = []
        
        # Determine images to upload
        if custom_img_list:
            image_glob = custom_img_list
            existing_images = []
        else:
            image_glob = glob.glob("*.png")
            if 'POSTER.png' in image_glob:
                image_glob.remove('POSTER.png')
            existing_images = meta.get('image_list', [])

        if len(existing_images) >= total_screens:
            return existing_images, total_screens

        if img_host == 'imgbox':
            nest_asyncio.apply()
            console.print("[green]Uploading Screens to Imgbox...")
            image_list = await self._imgbox_upload(f"{self.base_dir}/tmp/{meta['uuid']}", image_glob[-screens:])
            if not image_list:
                console.print("[yellow]Imgbox failed, trying next image host")
                return await self.upload_screenshots(meta, screens, img_host_num + 1, custom_img_list, total_screens)
        else:
            with Progress(TextColumn("[bold green]Uploading Screens..."), BarColumn(), "[cyan]{task.completed}/{task.total}", TimeRemainingColumn()) as progress:
                upload_task = progress.add_task(f"[green]Uploading Screens to {img_host}...", total=len(image_glob[-screens:]))
                i = 0
                
                for image in image_glob[-screens:]:
                    upload_result = await self._upload_to_host(img_host, image)
                    if isinstance(upload_result, list):  # Fallback case
                        newhost_list.extend(upload_result)
                    elif upload_result:
                        image_list.append(upload_result)
                        i += 1
                    progress.advance(upload_task)
                    time.sleep(0.5)
                    if i >= total_screens:
                        break

        final_list = image_list + newhost_list if newhost_list else image_list
        return_dict['image_list'] = final_list
        return final_list, i

    async def _upload_to_host(self, img_host, image):
        """Handle upload to specific image hosts."""
        try:
            if img_host == "imgbb":
                return await self._upload_imgbb(image)
            elif img_host == "oeimg":
                return await self._upload_oeimg(image)
            elif img_host == "ptscreens":
                return await self._upload_ptscreens(image)
            elif img_host == "freeimage.host":
                console.print("[red]Support for freeimage.host has been removed. Please remove from your config")
                time.sleep(15)
                return await self.upload_screenshots(meta, screens, img_host_num + 1, [], total_screens)
            elif img_host == "pixhost":
                return await self._upload_pixhost(image)
            elif img_host == "ptpimg":
                return await self._upload_ptpimg(image)
            elif img_host == "lensdump":
                return await self._upload_lensdump(image)
            else:
                console.print("[bold red]Please choose a supported image host in your config")
                raise SystemExit
        except Exception as e:
            console.print(f"[yellow]{img_host} failed, trying next image host: {str(e)}")
            return await self.upload_screenshots(meta, screens - i, img_host_num + 1, [], total_screens)

    async def _upload_imgbb(self, image):
        url = "https://api.imgbb.com/1/upload"
        data = {
            'key': self.config['DEFAULT']['imgbb_api'],
            'image': base64.b64encode(open(image, "rb").read()).decode('utf8')
        }
        response = requests.post(url, data=data, timeout=self.timeout).json()
        if not response.get('success'):
            raise ValueError(response)
        return {
            'web_url': response['data']['url_viewer'],
            'img_url': response['data'].get('medium', response['data']['image'])['url'],
            'raw_url': response['data']['image']['url']
        }

    async def _upload_oeimg(self, image):
        url = "https://imgoe.download/api/1/upload"
        data = {
            'key': self.config['DEFAULT']['oeimg_api'],
            'image': base64.b64encode(open(image, "rb").read()).decode('utf8')
        }
        response = requests.post(url, data=data, timeout=self.timeout).json()
        if response.get('status_code') != 200:
            raise ValueError(response)
        return {
            'web_url': response['data']['url_viewer'],
            'img_url': response['data'].get('medium', response['data']['image'])['url'],
            'raw_url': response['data']['image']['url']
        }

    async def _upload_ptscreens(self, image):
        url = "https://ptscreens.com/api/1/upload"
        data = {
            'key': self.config['DEFAULT']['ptscreens_api'],
            'image': base64.b64encode(open(image, "rb").read()).decode('utf8')
        }
        response = requests.post(url, data=data, timeout=self.timeout).json()
        if response.get('status_code') != 200:
            raise ValueError(response)
        return {
            'web_url': response['data']['url_viewer'],
            'img_url': response['data'].get('medium', response['data']['image'])['url'],
            'raw_url': response['data']['image']['url']
        }

    async def _upload_pixhost(self, image):
        url = "https://api.pixhost.to/images"
        data = {'content_type': '0', 'max_th_size': 350}
        files = {'img': ('file-upload[0]', open(image, 'rb'))}
        response = requests.post(url, data=data, files=files, timeout=self.timeout).json()
        if 'th_url' not in response:
            raise ValueError(response)
        return {
            'web_url': response['show_url'],
            'img_url': response['th_url'],
            'raw_url': response['th_url'].replace('https://t', 'https://img').replace('/thumbs/', '/images/')
        }

    async def _upload_ptpimg(self, image):
        url = "https://ptpimg.me/upload.php"
        payload = {'format': 'json', 'api_key': self.config['DEFAULT']['ptpimg_api']}
        files = [('file-upload[0]', open(image, 'rb'))]
        headers = {'referer': 'https://ptpimg.me/index.php'}
        response = requests.post(url, headers=headers, data=payload, files=files).json()
        ptpimg_code = response[0]['code']
        ptpimg_ext = response[0]['ext']
        img_url = f"https://ptpimg.me/{ptpimg_code}.{ptpimg_ext}"
        return {
            'web_url': img_url,
            'img_url': img_url,
            'raw_url': img_url
        }

    async def _upload_lensdump(self, image):
        url = "https://lensdump.com/api/1/upload"
        data = {'image': base64.b64encode(open(image, "rb").read()).decode('utf8')}
        headers = {'X-API-Key': self.config['DEFAULT']['lensdump_api']}
        response = requests.post(url, data=data, headers=headers, timeout=self.timeout).json()
        if response.get('status_code') != 200:
            raise ValueError(response)
        return {
            'web_url': response['data']['url_viewer'],
            'img_url': response['data'].get('medium', response['data']['image'])['url'],
            'raw_url': response['data']['image']['url']
        }

    async def _imgbox_upload(self, chdir, image_glob):
        os.chdir(chdir)
        image_list = []
        async with pyimgbox.Gallery(thumb_width=350, square_thumbs=False) as gallery:
            async for submission in gallery.add(image_glob):
                if not submission['success']:
                    console.print(f"[red]There was an error uploading to imgbox: [yellow]{submission['error']}[/yellow][/red]")
                    return []
                image_list.append({
                    'web_url': submission['web_url'],
                    'img_url': submission['thumbnail_url'],
                    'raw_url': submission['image_url']
                })
        return image_list

    async def upload_proof_or_cover(self, meta, image_key, file_key, file_exts=('jpg', '.jpeg')):
        """
        Upload a proof or cover image and update meta with the raw URL.
        
        Args:
            meta: Metadata dictionary
            image_key: Key to store the uploaded URL in meta (e.g., 'proof_image')
            file_key: Key containing the file path in meta (e.g., 'proof_file')
            file_exts: Tuple of acceptable file extensions
        """
        if image_key in meta and meta[image_key]:
            return

        image_path = meta.get(file_key)
        if not (image_path and os.path.isfile(image_path) and image_path.lower().endswith(file_exts)):
            return

        console.print(f"[yellow]Uploading {file_key.replace('_', ' ')} image: {image_path}")
        image_list, _ = await self.upload_screenshots(meta, 1, 1, [image_path], 1)
        
        if image_list:
            meta[image_key] = image_list[0]['raw_url']
            console.print(f"[green]Successfully uploaded and set {file_key.replace('_', ' ')} image: {meta[image_key]}")
        else:
            console.print(f"[red]Failed to upload {file_key.replace('_', ' ')} image.")
