import asyncio
import glob
import json
import os
import random
import re
from functools import partial
from hashlib import md5
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import ffmpeg
import oxipng
import numpy as np
from PIL import Image


from src.console import console, log


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
        self.child_processes = []

    async def generate_screenshots(self, meta, filename, folder_id, bdinfo=None, task_widget=None, count=None):
        screens = count if count is not None else meta.get('screens', self.config.get('DEFAULT', {}).get('screens', 3))
        img_host = meta.get('imghost') or self.config['DEFAULT'].get('img_host_1', 'imgbox')
        self.max_image_size = self.max_sizes.get(img_host.lower(), 10000000)

        tmp_dir = self.base_dir / "tmp" / folder_id
        tmp_dir.mkdir(parents=True, exist_ok=True)
        os.chdir(tmp_dir)

        existing_screenshots = sorted(glob.glob(f"{filename}-*.png"))
        valid_existing = [
            img for img in existing_screenshots 
            if os.path.exists(img) and self.min_image_size <= os.path.getsize(img) <= self.max_image_size
        ]
        if len(valid_existing) >= screens:
            log.debug(f"Reusing {len(valid_existing)} existing screenshots for {filename}")
            if task_widget:
                task_widget.text = f"Processing {filename}: Reusing existing screenshots"
            else:
                console.print(f"[green]Reusing {len(valid_existing)} existing screenshots for {filename}[/green]")
            return valid_existing[:screens]

        num_screens_needed = screens - len(valid_existing)
        log.debug(f"Found {len(valid_existing)} valid screenshots, generating {num_screens_needed} more for {filename} in {folder_id}")

        if task_widget:
            task_widget.text = f"Processing {filename}: Generating screenshots..."
        else:
            console.print(f"[yellow]Generating {num_screens_needed} screenshots for {filename}[/yellow]")

        if meta.get('is_disc') == 'BDMV':
            new_screenshots = await self._generate_bdmv(meta, filename, folder_id, bdinfo, num_screens_needed)
        elif meta.get('is_disc') == 'DVD':
            new_screenshots = await self._generate_dvd(meta, filename, num_screens_needed)
        else:
            new_screenshots = await self._generate_file(meta, filename, folder_id, num_screens_needed)

        all_screenshots = valid_existing + new_screenshots

        if self.config['SCREENS'].get('optimize_images', False):
            if task_widget:
                task_widget.text = f"Processing {filename}: Optimizing images..."
            else:
                console.print(f"[yellow]Optimizing images for {filename}[/yellow]")
            all_screenshots = await self._optimize_images(all_screenshots, meta.get('debug', False))
            if task_widget:
                task_widget.text = f"Processing {filename}: Images optimized"
            else:
                console.print(f"[green]Images optimized for {filename}[/green]")

        if task_widget:
            task_widget.text = f"Processing {filename}: Completed"
        else:
            console.print(f"[green]Generated {len(new_screenshots)} screenshots for {filename}[/green]")

        return all_screenshots[:screens]

    async def _generate_bdmv(self, meta, filename, folder_id, bdinfo, num_screens):
        file = self._find_longest_file(bdinfo)
        length = self._get_length(bdinfo)
        keyframe = 'nokey' if ("VC-1" in bdinfo['video'][0]['codec'] or bdinfo['video'][0]['hdr_dv'] != "") else 'none'

        ss_times = self._generate_valid_ss_times(length, num_screens)
        max_existing_idx = max([int(re.search(r'\d+', os.path.basename(f)).group()) for f in glob.glob(f"{filename}-*.png")] + [-1])
        tasks = [
            self._take_screenshot(file, f"{filename}-{max_existing_idx + 1 + i}.png", ss_time, keyframe, meta.get('ffdebug', False), hdr=meta['hdr'], chosen_times=meta.get('ss_times', None))
            for i, ss_time in enumerate(ss_times)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = await self._validate_and_retry(meta, results, file, filename, length, keyframe, num_screens)
        log.debug(f"Generated {len(valid)} valid BDMV screenshots out of {num_screens}")
        return valid

    async def _generate_dvd(self, meta, filename, num_screens):
        disc_info = meta['discs'][0]
        disc_path = disc_info['path']

        # Collect all VOBs from the disc
        vobs = []
        for vob_file in sorted(glob.glob(f"{disc_path}/VTS_*_*.VOB")):
            try:
                probe = ffmpeg.probe(vob_file)
                duration = float(probe['format']['duration'])
                if duration >= 60:  # Filter out VOBs shorter than 1 minute
                    vobs.append({'file': vob_file, 'duration': duration})
            except Exception as e:
                log.warning(f"Failed to probe VOB {vob_file}: {e}")

        if not vobs:
            log.error(f"No valid VOBs found in {disc_path}")
            raise ValueError("No VOBs with duration >= 60s found")

        # Total length for reference, but we'll work per-VOB
        total_length = sum(vob['duration'] for vob in vobs)
        vob_count = len(vobs)
        log.debug(f"Found {vob_count} valid VOBs with total length {total_length}s")

        # Distribute screenshots across VOBs
        screens_per_vob = max(1, num_screens // vob_count)
        extra_screens = num_screens % vob_count
        max_existing_idx = max([int(re.search(r'\d+', os.path.basename(f)).group()) for f in glob.glob(f"{filename}-*.png")] + [-1])
        tasks = []
        used_times = set()  # Track timestamps to ensure spread

        for vob_idx, vob in enumerate(vobs):
            num_for_vob = screens_per_vob + (1 if vob_idx < extra_screens else 0)
            vob_length = vob['duration']
            # Skip first 60s (credits) and last 10s
            start_buffer = min(60, vob_length * 0.1)
            end_buffer = max(vob_length - 10, vob_length * 0.9)

            # Generate unique timestamps for this VOB
            ss_times = []
            attempts = 0
            max_attempts = num_for_vob * 10
            while len(ss_times) < num_for_vob and attempts < max_attempts:
                ss_time = random.uniform(start_buffer, end_buffer)
                # Ensure at least 30s apart from previous times in this VOB
                if all(abs(ss_time - t) > 30 for t in ss_times):
                    ss_times.append(ss_time)
                attempts += 1

            if len(ss_times) < num_for_vob:
                log.warning(f"Could only generate {len(ss_times)} unique times for VOB {vob['file']}")
                ss_times.extend([random.uniform(start_buffer, end_buffer) for _ in range(num_for_vob - len(ss_times))])

            for i, ss_time in enumerate(sorted(ss_times)):
                output_idx = max_existing_idx + 1 + (vob_idx * screens_per_vob) + i
                output = f"{filename}-{output_idx}.png"
                tasks.append(
                    self._take_screenshot(vob['file'], output, ss_time, 'none', meta.get('ffdebug', False), vobs=vobs)
                )
                used_times.add((vob['file'], ss_time))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = await self._validate_and_retry(meta, results, vobs[0]['file'], filename, total_length, 'none', num_screens, vobs)

        unique_images = await self._filter_duplicates(valid, meta)
        if len(unique_images) < num_screens:
            log.debug(f"Only {len(unique_images)} unique screenshots, generating {num_screens - len(unique_images)} more")
            additional_tasks = []
            for i in range(num_screens - len(unique_images)):
                vob = random.choice(vobs)
                ss_time = random.uniform(60, vob['duration'] - 10)
                while any(abs(ss_time - t[1]) < 30 for t in used_times if t[0] == vob['file']):
                    ss_time = random.uniform(60, vob['duration'] - 10)
                output_idx = max_existing_idx + 1 + len(valid) + i
                additional_tasks.append(
                    self._take_screenshot(vob['file'], f"{filename}-{output_idx}.png", ss_time, 'none', meta.get('ffdebug', False), vobs=vobs)
                )
                used_times.add((vob['file'], ss_time))
            extra_results = await asyncio.gather(*additional_tasks, return_exceptions=True)
            valid.extend([r for r in extra_results if not isinstance(r, Exception) and r and os.path.exists(r)])

        log.debug(f"Generated {len(valid)} valid DVD screenshots from {vob_count} VOB{'s' if vob_count != 1 else ''}")
        return valid[:num_screens]


    async def _filter_duplicates(self, images, meta):
        """Filter out visually similar screenshots."""
        unique_images = []
        image_hashes = {}
        min_size = self.min_image_size

        for img in images:
            if not img or not os.path.exists(img) or os.path.getsize(img) < min_size:
                continue
            try:
                with Image.open(img) as im:
                    im = im.resize((32, 32), Image.Resampling.LANCZOS).convert('RGB')
                    hash_val = md5(np.array(im).tobytes()).hexdigest()
                if hash_val not in image_hashes:
                    image_hashes[hash_val] = img
                    unique_images.append(img)
                else:
                    log.debug(f"Duplicate image detected: {img}")
                    os.remove(img)
            except Exception as e:
                log.warning(f"Failed to process image {img} for duplicate check: {e}")
        return unique_images


    async def _generate_file(self, meta, filename, folder_id, num_screens):
        with open(f"{self.base_dir}/tmp/{folder_id}/MediaInfo.json", 'r', encoding='utf-8') as f:
            mi = json.load(f)
            length = float(mi['media']['track'][1].get('Duration', mi['media']['track'][0]['Duration']))
            path = mi['media']['@ref']

        ss_times = self._generate_valid_ss_times(length, num_screens, chosen_times=meta.get('ss_times', None))
        max_existing_idx = max([int(re.search(r'\d+', os.path.basename(f)).group()) for f in glob.glob(f"{filename}-*.png")] + [-1])
        log.debug(f"hdr= {meta['hdr']}")
        tasks = [
            self._take_screenshot(path, f"{filename}-{max_existing_idx + 1 + i}.png", ss_time, 'none', meta.get('ffdebug', False), hdr=meta['hdr'])
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

    async def _take_screenshot(self, file, output, ss_time, keyframe, ffdebug, retries=3, vobs=None, hdr=None):
        loglevel = 'verbose' if ffdebug or log.ddebug else 'error'
        vob_count = len(vobs) if vobs else 1
        initial_vob_idx = 0
        vf_filters = []

        # Deinterlacing for interlaced content
        if vobs:
            deinterlace_method = self.config['SCREENS'].get("deinterlace_method", "yadif")
            valid_deint_methods = {"yadif", "bwdif", "w3fdif", "nnedi"}
            
            if deinterlace_method not in valid_deint_methods:
                log.warning(f"Invalid deinterlace method '{deinterlace_method}', defaulting to yadif")
                deinterlace_method = "yadif"

            if deinterlace_method == "nnedi":
                weights_path = self.base_dir / "bin" / "nnedi3_weights.bin"
                if weights_path.exists():
                    vf_filters.append(f"nnedi=weights={weights_path}")
                else:
                    log.warning("nnedi weights not found, falling back to yadif")
                    vf_filters.append("yadif")
            else:
                vf_filters.append(deinterlace_method)

        # HDR tone-mapping
        if hdr not in (None, ""):
            hdr_method = self.config['SCREENS'].get("hdr_method", "default")
            if hdr_method not in (None, ""):
                valid_tonemap_methods = {"hable", "reinhard", "mobius"}

                if hdr_method == "linear":
                    vf_filters.append("zscale=transfer=linear:primaries=bt2020:range=full")
                else:
                    method_name = hdr_method.split(":")[0]
                    if method_name in valid_tonemap_methods:
                        vf_filters.append(f"zscale=transfer=linear, tonemap={hdr_method}")
                    else:
                        if hdr_method != "default":
                            log.warning(f"Invalid HDR method '{hdr_method}', defaulting to mobius:desat=9.8")
                        vf_filters.append("tonemap=mobius:desat=9.8")

                vf_filters.append("zscale=transfer=bt709:primaries=bt709:range=limited")

        vf_filters.append("scale=iw:ih")
        vf_filter = ",".join(vf_filters)
        log.debug(f"Applying video filter: {vf_filter}")

        if not os.path.exists(file):
            log.error(f"Video not found: {file}")
            return None

        for attempt in range(retries):
            current_file = file
            current_ss_time = ss_time
            if vobs and attempt > 0:
                initial_vob_idx = (initial_vob_idx + 1) % vob_count
                current_file = vobs[initial_vob_idx]['file']
                current_ss_time = random.uniform(0, vobs[initial_vob_idx]['duration'] - 1)

            log.debug(f"[magenta]ss_times[/magenta]: {str(current_ss_time)}")

            try:
                ffmpeg_args = [
                    "ffmpeg",
                    "-y",
                    "-loglevel", loglevel,
                    "-ss", str(current_ss_time),
                    "-i", current_file,
                    "-vframes", "1",
                    "-pix_fmt", "rgb24",
                    "-vf", vf_filter,
                    output
                ]
                if keyframe == 'nokey':
                    ffmpeg_args.insert(1, '-skip_frame')
                    ffmpeg_args.insert(2, 'nokey')

                log.ddebug(f"FFmpeg command: {' '.join(ffmpeg_args)}")

                process = await asyncio.create_subprocess_exec(
                    *ffmpeg_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                self.child_processes.append(process)
                stdout, stderr = await process.communicate()

                if process.returncode != 0:
                    stderr_str = stderr.decode('utf-8') if stderr else "No stderr output"
                    log.error(f"FFmpeg failed for {output} at {current_ss_time} (attempt {attempt + 1}): {stderr_str}")
                    raise ffmpeg.Error("FFmpeg execution failed", ffmpeg_args, stderr_str)

                if os.path.exists(output):
                    size = os.path.getsize(output)
                    if self.min_image_size <= size <= self.max_image_size:
                        log.debug(f"Screenshot {output} generated (size: {size} bytes) on attempt {attempt + 1}")
                        return output
                    log.debug(f"Screenshot {output} invalid size ({size} bytes) on attempt {attempt + 1}")
                    os.remove(output)

                if attempt == retries - 1:
                    log.debug(f"Failed to generate valid screenshot {output} after {retries} attempts")
                await asyncio.sleep(1)

            except ffmpeg.Error as e:
                if ffdebug:
                    log.error(f"FFmpeg error for {output} at {current_ss_time} (attempt {attempt + 1}): {e.stderr}")
                if os.path.exists(output):
                    os.remove(output)
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                log.debug(f"Screenshot task for {output} cancelled")
                if os.path.exists(output):
                    os.remove(output)
                raise
            except Exception as e:
                log.error(f"Unexpected error for {output} at {current_ss_time} (attempt {attempt + 1}): {str(e)}")
                if os.path.exists(output):
                    os.remove(output)
                await asyncio.sleep(1)
        return None

    def _generate_valid_ss_times(self, length, num_screens, chapters=None, avoid_times=None, start_buffer=None, end_buffer=None, chosen_times=None):
        if chosen_times is not None and len(chosen_times) >= num_screens:
            return sorted(chosen_times[:num_screens])
        if avoid_times is None:
            avoid_times = set()
        start_buffer = start_buffer if start_buffer is not None else max(length * 0.1, 10)
        end_buffer = end_buffer if end_buffer is not None else min(length * 0.9, length - 10)
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

        while len(ss_times) < num_screens and attempts < max_attempts:
            base_time = start_buffer + (len(ss_times) * interval)
            jitter = random.uniform(-interval / 4, interval / 4)
            ss_time = base_time + jitter
            if (start_buffer <= ss_time <= end_buffer and
                    all(abs(ss_time - t) > min_buffer for t in ss_times.union(avoid_times))):
                ss_times.add(ss_time)
            attempts += 1

        if len(ss_times) < num_screens:
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
        valid = []
        for r in results:
            if not isinstance(r, Exception) and r and os.path.exists(r):
                size = os.path.getsize(r)
                if self.min_image_size <= size <= self.max_image_size and not self.is_monochromatic(r):
                    valid.append(r)
                elif os.path.exists(r):
                    log.debug(f"Discarding {r}: {'monochromatic' if self.is_monochromatic(r) else 'invalid size'}")
                    os.remove(r)

        if len(valid) < num_screens:
            log.debug(f"Only {len(valid)} valid screenshots, retrying for {num_screens - len(valid)} more")
            missing = num_screens - len(valid)
            previous_times = {meta['ss_times'][i] for i, result in enumerate(results) if not result and i < len(meta.get('ss_times', []))}
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
                        self._take_screenshot(target_vob['file'], f"{filename}-{max_existing_idx + 1 + i}.png", adjusted_ss_time, keyframe, meta.get('ffdebug', False), vobs=vobs)
                    )
                    vob_idx += 1
                else:
                    tasks.append(
                        self._take_screenshot(file, f"{filename}-{max_existing_idx + 1 + i}.png", ss_time, keyframe, meta.get('ffdebug', False))
                    )
            retry_results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in retry_results:
                if not isinstance(r, Exception) and r and os.path.exists(r):
                    size = os.path.getsize(r)
                    if self.min_image_size <= size <= self.max_image_size and not self.is_monochromatic(r):
                        valid.append(r)
                    elif os.path.exists(r):
                        os.remove(r)
        return valid[:num_screens]

    def is_monochromatic(self, image_path, sample_size=100):
        """Check if an image is monochromatic by sampling pixels."""
        try:
            with Image.open(image_path) as img:
                img = img.convert('RGB')
                width, height = img.size
                if width * height < sample_size:
                    pixels = list(img.getdata())
                else:
                    pixels = [img.getpixel((random.randint(0, width - 1), random.randint(0, height - 1))) for _ in range(sample_size)]
                first_pixel = pixels[0]
                return all(pixel == first_pixel for pixel in pixels)
        except Exception as e:
            log.error(f"Error checking if {image_path} is monochromatic: {e}")
            return False


    async def _optimize_images(self, images, debug=False):
        valid_images = [img for img in images if img and os.path.exists(img)]
        if not valid_images:
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
            if debug:
                log.debug(f"Post-optimization: {len(self.child_processes)} ffmpeg processes still tracked")
                for proc in self.child_processes:
                    log.debug(f"PID {proc.pid} returncode: {proc.returncode}")
            return [r for r in optimized_results if not isinstance(r, Exception) and r]
        except asyncio.CancelledError:
            log.debug("Optimization cancelled")
            raise
        finally:
            executor.shutdown(wait=False) 
            self.child_processes.clear()

    async def _optimize_image_task(self, image, executor):
        try:
            level = 6 if os.path.getsize(image) >= 16000000 else 2
            optimize_with_level = partial(oxipng.optimize, image, level=level)
            await asyncio.get_running_loop().run_in_executor(executor, optimize_with_level)
            return image
        except Exception as e:
            log.error(f"Error optimizing {image}: {e}")
            return e
