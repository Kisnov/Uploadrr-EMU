import asyncio
import glob
import importlib
import json
import os
import random
import re
import signal
from functools import partial
from concurrent.futures import ProcessPoolExecutor

import ffmpeg
import httpx
import oxipng
import psutil
from rich.live import Live
from rich.spinner import Spinner

from .console import console, log
from .imghosts.base import BaseImageHost  # noqa: F401


class ScreenshotManager:
    def __init__(self, config, base_dir):
        self.base_dir = base_dir
        self.generator = ScreenshotGenerator(config, base_dir)
        self.uploader = ScreenshotUploadrr(config, base_dir)
        self._loop = asyncio.get_running_loop()
        self._interrupted = False
        self._register_signal_handlers()

    def _register_signal_handlers(self):
        """Register SIGINT handler at the manager level."""
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _handle_sigint(self, sig, frame):
        """Handle SIGINT by stopping tasks and triggering cleanup."""
        log.debug("Received SIGINT in manager, shutting down...")
        self._interrupted = True
        for task in asyncio.all_tasks(self._loop):
            task.cancel()

    async def handle_screenshots(self, meta):
        """Handle screenshot generation and uploading."""
        screens = meta.get('screens')
        try:
            screenshots = await self.generator.generate_screenshots(meta, meta['filename'], meta['uuid'], meta.get('bdinfo'))
            if self._interrupted:
                log.debug("Interrupted after screenshot generation, skipping upload")
                return meta
            if not meta.get('skip_imghost_upload', False):
                meta['image_list'] = await self.uploader.upload_screenshots(meta, screens)
            else:
                meta['image_list'] = []
            return meta
        except asyncio.CancelledError:
            log.debug("Screenshot handling cancelled")
            raise
        finally:
            await self._cleanup()

    async def shutdown(self):
        """Clean up resources on shutdown."""
        await self.uploader.shutdown()
        await self.generator._kill_all_child_processes()

    async def _cleanup(self):
        """Perform cleanup after operations."""
        await self.generator._kill_all_child_processes()
        if self._interrupted:
            log.debug("Cleanup triggered due to interruption")


class ScreenshotGenerator:
    def __init__(self, config, base_dir):
        self.config = config
        self.base_dir = base_dir
        self.min_image_size = 75000
        self.max_sizes = {
            "imgbb": 31000000, "imgbox": 10000000, "pixhost": 10000000,
            "ptscreens": 10000000, "oeimg": 10000000
        }
        self.max_image_size = None
        self._interrupted = False
        self._loop = asyncio.get_running_loop()  # Use existing loop
        self._register_signal_handlers()

    def _register_signal_handlers(self):
        """Register SIGINT handler using signal.signal."""
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _handle_sigint(self, sig, frame):
        """Handle SIGINT by setting the interrupted flag and cancelling tasks."""
        log.debug("Received SIGINT in generator, initiating shutdown...")
        self._interrupted = True
        for task in asyncio.all_tasks(self._loop):
            task.cancel()

    async def generate_screenshots(self, meta, filename, folder_id, bdinfo=None):
        screens = meta.get('screens', self.config.get('DEFAULT', {}).get('screens', 3))
        img_host = meta.get('imghost') or self.config['DEFAULT'].get('img_host_1', 'imgbox')
        self.max_image_size = self.max_sizes.get(img_host.lower(), 10000000)

        """Generate or reuse screenshots with full validation."""
        tmp_dir = f"{self.base_dir}/tmp/{folder_id}"
        os.makedirs(tmp_dir, exist_ok=True)
        os.chdir(tmp_dir)

        existing_screenshots = sorted(glob.glob(f"{filename}-*.png"))
        valid_existing = []
        for img in existing_screenshots:
            if os.path.exists(img):
                size = os.path.getsize(img)
                if self.min_image_size <= size <= self.max_image_size:
                    valid_existing.append(img)
                else:
                    log.debug(f"Removing invalid existing screenshot {img} (size: {size} bytes)")
                    os.remove(img)

        num_screens_needed = screens - len(valid_existing)
        if num_screens_needed <= 0:
            log.debug(f"Reusing {len(valid_existing)} existing valid screenshots for {filename}")
            return valid_existing[:screens]

        log.debug(f"Found {len(valid_existing)} valid existing screenshots, generating {num_screens_needed} more for {filename} in {folder_id}")
        live = Live(Spinner("dots", text="Generating screenshots..."), transient=True)
        with live:
            if self._interrupted:
                live.stop()
                log.debug("Interrupted before generation, returning existing screenshots")
                return valid_existing

            try:
                if meta.get('is_disc') == 'BDMV':
                    new_screenshots = await self._generate_bdmv(meta, filename, folder_id, bdinfo, num_screens_needed)
                elif meta.get('is_disc') == 'DVD':
                    new_screenshots = await self._generate_dvd(meta, filename, num_screens_needed)
                else:
                    new_screenshots = await self._generate_file(meta, filename, folder_id, num_screens_needed)

                if self._interrupted:
                    live.stop()
                    log.debug("Interrupted during generation, skipping optimization")
                    return valid_existing + new_screenshots

                all_screenshots = valid_existing + new_screenshots
                if self.config['SCREENS'].get('optimize_images', False):
                    all_screenshots = await self._optimize_images(all_screenshots, meta.get('debug', False))

                return all_screenshots[:screens]
            except asyncio.CancelledError:
                live.stop()
                log.debug("Screenshot generation cancelled")
                raise

    async def _generate_bdmv(self, meta, filename, folder_id, bdinfo, num_screens):
        file = self._find_longest_file(bdinfo)
        length = self._get_length(bdinfo)
        keyframe = 'nokey' if ("VC-1" in bdinfo['video'][0]['codec'] or bdinfo['video'][0]['hdr_dv'] != "") else 'none'
        
        ss_times = self._generate_valid_ss_times(length, num_screens)
        max_existing_idx = max([int(re.search(r'\d+', os.path.basename(f)).group()) for f in glob.glob(f"{filename}-*.png")] + [-1])
        tasks = [
            self._take_screenshot(file, f"{filename}-{max_existing_idx + 1 + i}.png", ss_time, keyframe, meta.get('ffdebug', False))
            for i, ss_time in enumerate(ss_times)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = await self._validate_and_retry(meta, results, file, filename, length, keyframe, num_screens)
        log.debug(f"Generated {len(valid)} valid BDMV screenshots out of {num_screens}")
        return valid

    async def _generate_dvd(self, meta, filename, num_screens):
        disc_info = meta['discs'][0]
        disc_path = disc_info['path']
        main_set = disc_info.get('main_set', [])
        chapters = disc_info.get('chapters', [])

        vobs = []
        if main_set:
            vob_pattern = f"{disc_path}/VTS_{main_set[0][:2]}_*.VOB"
            vob_files = sorted(glob.glob(vob_pattern))
            for vob_file in vob_files:
                try:
                    probe = ffmpeg.probe(vob_file)
                    duration = float(probe['format']['duration'])
                    vobs.append({'file': vob_file, 'duration': duration})
                except Exception as e:
                    log.warning(f"Failed to probe VOB {vob_file}: {e}")
        if not vobs:
            vob_file = f"{disc_path}/VTS_01_1.VOB"
            if os.path.exists(vob_file):
                try:
                    probe = ffmpeg.probe(vob_file)
                    duration = float(probe['format']['duration'])
                    vobs = [{'file': vob_file, 'duration': duration}]
                except Exception as e:
                    log.error(f"Failed to probe fallback VOB {vob_file}: {e}")
            else:
                log.error(f"No VOBs found and fallback {vob_file} unavailable")
                raise ValueError("Cannot determine DVD length without VOB files")

        length = sum(vob['duration'] for vob in vobs)
        if length <= 0:
            raise ValueError("Invalid DVD length")

        ss_times = self._generate_valid_ss_times(length, num_screens, chapters)
        max_existing_idx = max([int(re.search(r'\d+', os.path.basename(f)).group()) for f in glob.glob(f"{filename}-*.png")] + [-1])
        tasks = []
        vob_count = len(vobs)
        for i, ss_time in enumerate(ss_times):
            vob_idx = i % vob_count if vob_count > 1 else 0
            target_vob = vobs[vob_idx]
            cumulative_time = sum(v['duration'] for v in vobs[:vob_idx])
            adjusted_ss_time = min(max(ss_time - cumulative_time, 0), target_vob['duration'] - 1)
            tasks.append(
                self._take_screenshot(
                    target_vob['file'],
                    f"{filename}-{max_existing_idx + 1 + i}.png",
                    adjusted_ss_time,
                    'none',
                    meta.get('ffdebug', False),
                    vobs=vobs if vob_count > 1 else None
                )
            )
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = await self._validate_and_retry(meta, results, vobs[0]['file'], filename, length, 'none', num_screens, vobs)
        log.debug(f"Generated {len(valid)} valid DVD screenshots from {vob_count} VOB{'s' if vob_count != 1 else ''}")
        return valid

    async def _generate_file(self, meta, filename, folder_id, num_screens):
        with open(f"{self.base_dir}/tmp/{folder_id}/MediaInfo.json", 'r', encoding='utf-8') as f:
            mi = json.load(f)
            length = float(mi['media']['track'][1].get('Duration', mi['media']['track'][0]['Duration']))
            path = mi['media']['@ref']

        ss_times = self._generate_valid_ss_times(length, num_screens)
        max_existing_idx = max([int(re.search(r'\d+', os.path.basename(f)).group()) for f in glob.glob(f"{filename}-*.png")] + [-1])
        tasks = [
            self._take_screenshot(path, f"{filename}-{max_existing_idx + 1 + i}.png", ss_time, 'none', meta.get('ffdebug', False))
            for i, ss_time in enumerate(ss_times)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = await self._validate_and_retry(meta, results, path, filename, length, 'none', num_screens)
        log.debug(f"Generated {len(valid)} valid file screenshots")
        return valid

    def _find_longest_file(self, bdinfo):
        length = 0
        file = None
        for each in bdinfo['files']:
            int_length = sum(float(x) * 60 ** i for i, x in enumerate(reversed(each['length'].split(':'))))
            if int_length > length:
                length = int_length
                for root, _, files in os.walk(bdinfo['path']):
                    for name in files:
                        if name.lower() == each['file'].lower():
                            file = f"{root}/{name}"
        return file

    def _get_length(self, bdinfo):
        length = 0
        for each in bdinfo['files']:
            int_length = sum(float(x) * 60 ** i for i, x in enumerate(reversed(each['length'].split(':'))))
            if int_length > length:
                length = int_length
        return length

    async def _take_screenshot(self, file, output, ss_time, keyframe, ffdebug, retries=3, vobs=None):
        loglevel = 'verbose' if ffdebug else 'quiet'
        vob_count = len(vobs) if vobs else 1
        initial_vob_idx = 0 if vobs else None

        if not os.path.exists(file):
            log.error(f"Video not found: {file}")
            return None

        for attempt in range(retries):
            if self._interrupted:
                log.debug(f"Interrupted, cancelling screenshot {output}")
                return None

            current_file = file
            current_ss_time = ss_time
            if vobs and attempt > 0:
                initial_vob_idx = (initial_vob_idx + 1) % vob_count
                current_file = vobs[initial_vob_idx]['file']
                current_ss_time = random.uniform(0, vobs[initial_vob_idx]['duration'] - 1)

            try:
                ffmpeg_args = [
                    'ffmpeg',
                    '-ss', str(current_ss_time),
                    '-i', current_file,
                    '-vframes', '1',
                    '-pix_fmt', 'rgb24',
                    '-vf', 'scale=iw:ih',
                    '-loglevel', loglevel,
                    '-y',
                    output
                ]
                if keyframe == 'nokey':
                    ffmpeg_args.insert(2, '-skip_frame')
                    ffmpeg_args.insert(3, 'nokey')

                process = await asyncio.create_subprocess_exec(
                    *ffmpeg_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()

                if self._interrupted:
                    log.debug(f"Interrupted during FFmpeg execution for {output}")
                    if process.returncode is None:  # Process still running
                        process.terminate()
                    return None

                if process.returncode != 0:
                    stderr_str = stderr.decode('utf-8') if stderr else "No stderr output"
                    if ffdebug or "fatal" in stderr_str.lower():
                        log.error(f"FFmpeg failed for {output} at {current_ss_time} (attempt {attempt + 1}): {stderr_str}")
                    else:
                        log.debug(f"FFmpeg failed silently for {output} at {current_ss_time} (attempt {attempt + 1})")
                    raise ffmpeg.Error("FFmpeg execution failed", cmd=ffmpeg_args, stderr=stderr_str)

                if os.path.exists(output):
                    size = os.path.getsize(output)
                    if self.min_image_size <= size <= self.max_image_size:
                        log.debug(f"\nScreenshot {output} generated successfully (size: {size} bytes) on attempt {attempt + 1}")
                        return output
                    if log.debug:
                        console.print()
                        log.debug(f"Screenshot {output} invalid size ({size} bytes) on attempt {attempt + 1}")
                    os.remove(output)
                if attempt == retries - 1:
                    if log.debug:
                        console.print()
                        log.debug(f"Failed to generate valid screenshot {output} after {retries} attempts")
                await asyncio.sleep(1)
            except ffmpeg.Error as e:
                if ffdebug:
                    log.error(f"Failed to generate screenshot {output} at {current_ss_time} (attempt {attempt + 1}): FFmpeg error - {e.stderr}")
                else:
                    log.debug(f"FFmpeg error suppressed for {output} at {current_ss_time} (attempt {attempt + 1})")
                if os.path.exists(output):
                    os.remove(output)
                if attempt == retries - 1:
                    log.debug(f"Exhausted retries for {output} after {retries} attempts")
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                log.debug(f"Screenshot task for {output} cancelled")
                if os.path.exists(output):
                    os.remove(output)
                raise
            except Exception as e:
                log.error(f"Unexpected error generating screenshot {output} at {current_ss_time} (attempt {attempt + 1}): {e}")
                if os.path.exists(output):
                    os.remove(output)
                if attempt == retries - 1:
                    log.debug(f"Exhausted retries for {output} after {retries} attempts")
                await asyncio.sleep(1)
        return None

    def _generate_valid_ss_times(self, length, num_screens, chapters=None, avoid_times=None):
        if avoid_times is None:
            avoid_times = set()
        
        if length < 60:
            log.warning(f"Media length {length}s is too short for screenshots")
            return [random.uniform(0, length) for _ in range(num_screens)]

        start_buffer = max(length * 0.1, 10)
        end_buffer = min(length * 0.9, length - 10)
        valid_range = end_buffer - start_buffer
        
        if valid_range < num_screens * 10:
            log.warning(f"Video length {length}s too short for {num_screens} spaced screenshots")
            start_buffer = 10
            end_buffer = length - 10
            valid_range = end_buffer - start_buffer

        if chapters and len(chapters) >= num_screens:
            chapter_times = [self._parse_time(chapter['time']) for chapter in chapters]
            valid_times = []
            for i in range(num_screens):
                chapter_idx = i * (len(chapters) // num_screens)
                base_time = chapter_times[chapter_idx]
                duration = (chapter_times[chapter_idx + 1] - base_time) if chapter_idx + 1 < len(chapters) else (length - base_time)
                valid_times.append(base_time + duration / 2)
            return sorted(valid_times)

        interval = valid_range / max(num_screens, 1)
        ss_times = set()
        attempts = 0
        max_attempts = num_screens * 10
        min_buffer = 10

        while len(ss_times) < num_screens and attempts < max_attempts and not self._interrupted:
            base_time = start_buffer + (len(ss_times) * interval)
            jitter = random.uniform(-interval / 4, interval / 4)
            ss_time = base_time + jitter
            if (start_buffer <= ss_time <= end_buffer and 
                all(abs(ss_time - t) > min_buffer for t in ss_times.union(avoid_times))):
                ss_times.add(ss_time)
            attempts += 1

        if len(ss_times) < num_screens and not self._interrupted:
            log.warning(f"Could only generate {len(ss_times)} unique screenshot times")
            while len(ss_times) < num_screens:
                ss_time = random.uniform(start_buffer, end_buffer)
                if all(abs(ss_time - t) > min_buffer for t in ss_times.union(avoid_times)):
                    ss_times.add(ss_time)

        return sorted(list(ss_times))

    def _parse_time(self, time_str):
        match = re.match(r'(\d+):(\d+):(\d+\.\d+|\d+)', time_str)
        if match:
            hours, minutes, seconds = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        return 0

    async def _validate_and_retry(self, meta, results, file, filename, length, keyframe, num_screens, vobs=None):
        valid = [r for r in results if not isinstance(r, Exception) and r and os.path.exists(r) and self.min_image_size <= os.path.getsize(r) <= self.max_image_size]
        if len(valid) < num_screens and not self._interrupted:
            log.debug(f"Only {len(valid)} valid screenshots, retrying for {num_screens - len(valid)} more")
            missing = num_screens - len(valid)
            previous_times = set()
            for i, result in enumerate(results):
                if not result and i < len(meta.get('ss_times', [])):
                    previous_times.add(meta['ss_times'][i])
            new_ss_times = self._generate_valid_ss_times(length, missing, avoid_times=previous_times)
            meta['ss_times'] = new_ss_times
            tasks = []
            vob_idx = len(valid) if vobs else 0
            max_existing_idx = max([int(re.search(r'\d+', os.path.basename(f)).group()) for f in glob.glob(f"{filename}-*.png")] + [-1])
            for i, ss_time in enumerate(new_ss_times):
                if vobs and len(vobs) > 1:
                    target_vob = vobs[vob_idx % len(vobs)]
                    cumulative_time = sum(v['duration'] for v in vobs[:vob_idx % len(vobs)])
                    adjusted_ss_time = min(max(ss_time - cumulative_time, 0), target_vob['duration'] - 1)
                    tasks.append(
                        self._take_screenshot(
                            target_vob['file'],
                            f"{filename}-{max_existing_idx + 1 + i}.png",
                            adjusted_ss_time,
                            keyframe,
                            meta.get('ffdebug', False),
                            vobs=vobs
                        )
                    )
                    vob_idx += 1
                else:
                    tasks.append(
                        self._take_screenshot(
                            file,
                            f"{filename}-{max_existing_idx + 1 + i}.png",
                            ss_time,
                            keyframe,
                            meta.get('ffdebug', False)
                        )
                    )
            retry_results = await asyncio.gather(*tasks, return_exceptions=True)
            valid.extend([r for r in retry_results if not isinstance(r, Exception) and r and os.path.exists(r) and self.min_image_size <= os.path.getsize(r) <= self.max_image_size])
        return valid[:num_screens]

    async def _optimize_images(self, images, debug=False):
        valid_images = [img for img in images if img and os.path.exists(img)]
        if not valid_images or self._interrupted:
            return images
        threads = self.config['DEFAULT'].get('threads', 4)
        if threads == 'max':
            threads = os.cpu_count()

        num_workers = min(threads, len(valid_images))
        if debug:
            log.debug(f"Optimizing {len(valid_images)} images with {num_workers} workers")

        executor = ProcessPoolExecutor(max_workers=num_workers)
        tasks = [asyncio.create_task(self._optimize_image_task(img, executor)) for img in valid_images]

        try:
            optimized_results = await asyncio.gather(*tasks, return_exceptions=True)
            if self._interrupted:
                log.debug("Interrupted during optimization, stopping")
                executor.shutdown(wait=False)
                await self._kill_all_child_processes()
            return [r for r in optimized_results if not isinstance(r, Exception) and r]
        except asyncio.CancelledError:
            log.debug("Optimization cancelled")
            executor.shutdown(wait=False)
            await self._kill_all_child_processes()
            raise
        finally:
            executor.shutdown(wait=True)
            await self._kill_all_child_processes()

    async def _optimize_image_task(self, image, executor):
        try:
            if self._interrupted:
                return None
            level = 6 if os.path.getsize(image) >= 16000000 else 2
            optimize_with_level = partial(oxipng.optimize, image, level=level)
            await asyncio.get_running_loop().run_in_executor(executor, optimize_with_level)
            return image
        except asyncio.CancelledError:
            log.debug(f"Optimization task for {image} cancelled")
            raise
        except Exception as e:
            log.error(f"Error optimizing {image}: {e}")
            return e

    async def _kill_all_child_processes(self):
        """Ensure all child processes are terminated."""
        current_process = psutil.Process()
        children = current_process.children(recursive=True)
        for child in children:
            if 'oxipng' in child.name().lower() or 'ffmpeg' in child.name().lower() == os.getpid():
                log.debug(f"Killing process {child.pid} ({child.name()})")
                child.terminate()
        gone, still_alive = psutil.wait_procs(children, timeout=1)
        for process in still_alive:
            log.debug(f"Force killing stubborn process {process.pid}")
            process.kill()

class ScreenshotUploadrr:
    def __init__(self, config, base_dir):
        self.config = config
        self.base_dir = base_dir
        self.timeout = httpx.Timeout(60.0)
        self.min_image_size = 75000
        self.client = None
        self._interrupted = False
        self._loop = asyncio.get_running_loop()
        self._register_signal_handlers()

    def _register_signal_handlers(self):
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _handle_sigint(self, sig, frame):
        log.debug("Received SIGINT, initiating shutdown...")
        self._interrupted = True
        for task in asyncio.all_tasks(self._loop):
            task.cancel()

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client and not self.client.is_closed:
            await self.client.aclose()

    def _load_host(self, host_name):
        try:
            module = importlib.import_module(f"src.imghosts.{host_name}")
            host_class = getattr(module, host_name.upper())
            return host_class(self.config)
        except (ImportError, AttributeError) as e:
            log.warning(f"Failed to load host '{host_name}': {str(e)}")
            return None

    async def upload_screenshots(self, meta, screens, img_path=None):
        img_host = meta.get('imghost')
        max_retries = 3
        uploaded_images = []

        if img_path:
            if not (os.path.isfile(img_path) and os.path.getsize(img_path) >= self.min_image_size):
                log.debug(f"Invalid or undersized cover file: {img_path}")
                return []
            to_upload = [img_path]
            log.debug(f"Uploading cover image: {img_path}")
        else:
            os.chdir(f"{self.base_dir}/tmp/{meta['uuid']}")
            images = glob.glob("*.png")
            existing = meta.get('image_list', [])
            log.debug(f"Existing screenshots: {len(existing)}")
            if len(existing) >= screens:
                log.debug(f"All {len(existing)} screenshots already uploaded")
                return existing
            to_upload = [
                img for img in images
                if img not in [e.get('local_file') for e in existing]
                and os.path.getsize(img) >= self.min_image_size
            ][:screens - len(existing)]
            log.debug(f"Found {len(to_upload)} screenshots to upload")

        if not to_upload or self._interrupted:
            log.debug(f"No new screenshots to upload or interrupted, returning {len(existing)} existing")
            return existing

        log.debug(f"Starting upload of {len(to_upload)} screenshots")
        async with self:
            with Live(Spinner("dots", text="Uploading screenshots..."), transient=True):
                # Try preferred host
                if img_host:
                    log.debug(f"Attempting preferred host: {img_host}")
                    uploaded_images = await self._upload_with_retries(img_host, to_upload, max_retries)
                    if uploaded_images:
                        log.debug(f"Uploaded {len(uploaded_images)} images to '{img_host}'")

                # Fallback to configured hosts
                if not uploaded_images and not self._interrupted:
                    i = 1
                    while f'img_host_{i}' in self.config['SCREENS'] and not uploaded_images:
                        next_host = self.config['SCREENS'][f'img_host_{i}']
                        if next_host != img_host:
                            log.debug(f"Failing over to config host {i}: {next_host}")
                            uploaded_images = await self._upload_with_retries(next_host, to_upload, max_retries)
                            if uploaded_images:
                                log.debug(f"Uploaded {len(uploaded_images)} images to '{next_host}'")
                        i += 1

                # Final fallback to imgbox
                if not uploaded_images and not self._interrupted:
                    log.debug("Falling back to imgbox")
                    uploaded_images = await self._upload_with_retries("imgbox", to_upload, max_retries)

                if not uploaded_images and not self._interrupted:
                    log.debug("All host attempts failed, no screenshots uploaded")

                updated_list = existing + uploaded_images
                meta['image_list'] = updated_list
                log.debug(f"Completed upload: {len(uploaded_images)} new screenshots")
                return updated_list

    async def _upload_with_retries(self, host_name, images, max_retries):
        host = self._load_host(host_name)
        if not host:
            return []

        for attempt in range(1, max_retries + 1):
            if self._interrupted:
                log.debug(f"Interrupted during attempt {attempt} for '{host_name}'")
                return []

            log.debug(f"Attempt {attempt}/{max_retries} for '{host_name}' with {len(images)} images")
            uploaded_images = await self._upload_to_host(host, images)
            
            if uploaded_images:
                if len(uploaded_images) < len(images):
                    log.debug(f"Partial success: {len(uploaded_images)}/{len(images)} uploaded, retrying")
                else:
                    log.debug(f"Full success: {len(uploaded_images)} images uploaded")
                    return uploaded_images
            else:
                log.debug(f"All uploads failed for '{host_name}' on attempt {attempt}")
                if attempt == 1:
                    log.debug(f"Systemic failure assumed for '{host_name}', moving to next host")
                    return []
                log.debug(f"Retrying '{host_name}'")

        return uploaded_images

    async def _upload_to_host(self, host, images):
        tasks = [host.upload(self.client, img) for img in images]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        uploaded_images = []
        for result, img in zip(results, images):
            if self._interrupted:
                break
            if isinstance(result, dict):
                uploaded_images.append({
                    'img_url': result['img_url'],
                    'raw_url': result['raw_url'],
                    'web_url': result['web_url'],
                    'local_file': img
                })
            elif isinstance(result, Exception):
                log.debug(f"Upload failed for {img}: {str(result)}")
        return uploaded_images

    async def upload_proof_or_cover(self, meta, image_key, file_key, file_exts=('.jpg', '.jpeg')):
        if image_key in meta and meta[image_key]:
            log.debug(f"{image_key} already present in meta: {meta[image_key]}")
            return

        image_path = meta.get(file_key)
        if not (image_path and os.path.isfile(image_path) and image_path.lower().endswith(file_exts)):
            log.debug(f"Invalid or missing {file_key}: {image_path}")
            return

        from rich.console import Console
        console = Console()
        console.print(f"[yellow]Uploading {file_key.replace('_', ' ')} image: {image_path}")
        
        uploaded_images = await self.upload_screenshots(meta, 1, img_path=image_path)

        if uploaded_images:
            meta[image_key] = uploaded_images[0]['raw_url']
            console.print(f"[green]Successfully uploaded and set {file_key.replace('_', ' ')} image: {meta[image_key]}")
        else:
            console.print(f"[red]Failed to upload {file_key.replace('_', ' ')} image.")


    async def shutdown(self):
        if self.client and not self.client.is_closed:
            await self.client.aclose()
        await self._kill_all_child_processes()

    async def _kill_all_child_processes(self):
        current_process = psutil.Process()
        children = current_process.children(recursive=True)
        for child in children:
            if 'oxipng' in child.name().lower() or 'ffmpeg' in child.name().lower() or child.ppid() == os.getpid():
                log.debug(f"Killing process {child.pid} ({child.name()})")
                child.terminate()
        gone, still_alive = psutil.wait_procs(children, timeout=1)
        for process in still_alive:
            log.debug(f"Force killing process {process.pid}")
            process.kill()
