import asyncio
import glob
import os
import random
import re
from pathlib import Path
from typing import Dict, Tuple

import ffmpeg
import psutil
from PIL import Image
from pymediainfo import MediaInfo

from src.mediainfo import MediaInfoHandler
from src.progress import ProgressManager
from src.utils.console import log


class ScreenshotGenerator:
    def __init__(self, config, base_dir):
        self.config = config
        self.base_dir = base_dir
        self.tmp_dir = None
        self.min_image_size = 75000
        self.max_image_size = 10000000
        self.cache = {}  # Simple in-memory cache: {uuid: List[Dict]}

    async def generate_screenshots(self, meta: Dict, filename: str, uuid: str) -> Dict:
        """Generate screenshots for the given media, reusing existing ones if valid."""
        pm = ProgressManager.get_instance()
        if pm:
            pm.update(f"Starting screenshot generation for {meta.get('name', filename)}")

        screens = meta.get("screens", self.config.get("DEFAULT", {}).get("screens", 3))
        contact_screens = 0
        rows = columns = None
        if meta.get("xxx", False):
            contact_cfg = self.config["SCREENS"].get("contact_sheet", {})
            rows = contact_cfg.get("row")
            columns = contact_cfg.get("column")
            if rows is None or columns is None:
                length = self._get_main_video_length(meta)
                rows, columns = self._get_rows_columns(length)
            contact_screens = rows * columns

        self.tmpdir = tmp_dir = self.base_dir / "tmp" / uuid
        os.chdir(tmp_dir)

        cached = self.cache.get(uuid, [])
        existing = meta.get("image_list", {"main": [], "episodes": {}})
        if cached and self._is_sufficient(existing, meta, screens):
            if pm:
                pm.update("Using cached screenshots")
            return existing

        # -- GENERATION TYPE -- #
        if pm:
            pm.update("Generating screenshots")

        if meta.get("bdinfo") not in (None, ""):
            result = await self._generate_bdmv_screenshots(meta, filename, uuid, screens, contact_screens)
        elif meta.get("discs"):
            result = await self._generate_dvd_screenshots(meta, filename, uuid, screens, contact_screens)
        elif meta.get("tv_pack") == 1:
            result = await self._generate_tv_pack_screenshots(meta, filename, uuid, screens, contact_screens)
        else:
            result = await self._generate_file_screenshots(meta, filename, uuid, screens, contact_screens)

        # -- CONTACT SHEETS -- #
        if contact_screens > 0 and result.get("main"):
            if pm:
                pm.update("Creating contact sheet")
            await self._generate_contact_sheet(result["main"][:contact_screens], meta, tmp_dir / "contact_sheet.png", rows, columns)

        # -- OPTIMIZE -- #
        if self.config["SCREENS"].get("optimize", False):
            if pm:
                pm.update("Optimizing screenshots")
            optimize_tasks = [self._optimize_image((tmp_dir / img["local_file"])) for img in result["main"]]
            await asyncio.gather(*optimize_tasks)

        self.cache[uuid] = result
        if pm:
            pm.update("Screenshot generation complete")
        return result

    async def _generate_file_screenshots(self, meta, filename, uuid, screens, contact_screens):
        """Generate screenshots for a single file, reusing valid existing ones."""
        num_needed = max(screens, contact_screens)
        tmp_dir = self.base_dir / "tmp" / uuid

        existing_screenshots = [
            img
            for img in glob.glob(f"{tmp_dir}/{filename}-*.png")
            if Path(img).stat().st_size > 1000 and not self.is_monochromatic(img)
        ]
        num_existing = len(existing_screenshots)
        num_to_generate = num_needed - num_existing

        if num_to_generate > 0:
            new_screenshots = await self._generate_file(meta, filename, uuid, num_to_generate, meta.get("video"))
            all_screenshots = [
                {"local_file": Path(path).name, "ss_time": t}
                for path, t in [(img, 0) for img in existing_screenshots] + new_screenshots
            ]
        else:
            all_screenshots = [{"local_file": Path(img).name, "ss_time": 0} for img in existing_screenshots]

        return {"main": all_screenshots[:screens], "episodes": {}}

    async def _generate_tv_pack_screenshots(self, meta, filename, uuid, screens, contact_screens):
        """Generate screenshots for TV pack, reusing valid existing ones per episode."""
        per_episode = self.config["SCREENS"].get("per_episode", 3)

        tmp_dir = self.base_dir / "tmp" / uuid
        filelist = sorted(meta.get("filelist", []), key=self._get_episode_identifier)
        result = {"main": [], "episodes": {}}

        for ep_path in filelist:
            if not self._is_video_file(ep_path):
                continue
            ep_name = self._get_episode_identifier(ep_path)
            ep_filename = f"{filename}-{ep_name}"
            num_needed = max(screens, contact_screens) if ep_name == "E01" else per_episode

            # -- VALIDATION -- #
            existing_screenshots = [
                img
                for img in glob.glob(f"{tmp_dir}/{ep_filename}-*.png")
                if Path(img).stat().st_size > 1000 and not self.is_monochromatic(img)
            ]
            num_existing = len(existing_screenshots)
            num_to_generate = num_needed - num_existing

            if num_to_generate > 0:
                new_screenshots = await self._generate_file(meta, ep_filename, uuid, num_to_generate, ep_path)
                ep_screens = [
                    {"local_file": Path(path).name, "ss_time": t}
                    for path, t in [(img, 0) for img in existing_screenshots] + new_screenshots
                ]
            else:
                ep_screens = [{"local_file": Path(img).name, "ss_time": 0} for img in existing_screenshots]

            if ep_name == "E01":
                result["main"] = ep_screens[:screens]
            result["episodes"][ep_name] = ep_screens[:num_needed]

        return result

    async def _generate_bdmv_screenshots(self, meta, filename, uuid, screens, contact_screens):
        """Generate screenshots for BDMV, reusing valid existing ones."""
        num_needed = max(screens, contact_screens)
        tmp_dir = self.base_dir / "tmp" / uuid

        # -- VALIDATION -- #
        existing_screenshots = [
            img
            for img in glob.glob(f"{tmp_dir}/{filename}-*.png")
            if Path(img).stat().st_size > 1000 and not self.is_monochromatic(img)
        ]
        num_existing = len(existing_screenshots)
        num_to_generate = num_needed - num_existing

        if num_to_generate > 0:
            bdinfo = meta["bdinfo"]
            new_screenshots = await self._generate_bdmv(meta, filename, uuid, bdinfo, num_to_generate)
            all_screenshots = [
                {"local_file": Path(path).name, "ss_time": t}
                for path, t in [(img, 0) for img in existing_screenshots] + new_screenshots
            ]
        else:
            all_screenshots = [{"local_file": Path(img).name, "ss_time": 0} for img in existing_screenshots]

        return {"main": all_screenshots[:screens], "episodes": {}}

    async def _generate_dvd_screenshots(self, meta, filename, uuid, screens, contact_screens):
        """Generate screenshots for DVD, reusing valid existing ones."""
        num_needed = max(screens, contact_screens)
        tmp_dir = self.base_dir / "tmp" / uuid

        # -- VALIDATION -- #
        existing_screenshots = [
            img
            for img in glob.glob(f"{tmp_dir}/{filename}-*.png")
            if Path(img).stat().st_size > 1000 and not self.is_monochromatic(img)
        ]
        num_existing = len(existing_screenshots)
        num_to_generate = num_needed - num_existing

        if num_to_generate > 0:
            new_screenshots = await self._generate_dvd(meta, filename, num_to_generate)
            all_screenshots = [
                {"local_file": Path(path).name, "ss_time": t}
                for path, t in [(img, 0) for img in existing_screenshots] + new_screenshots
            ]
        else:
            all_screenshots = [{"local_file": Path(img).name, "ss_time": 0} for img in existing_screenshots]

        return {"main": all_screenshots[:screens], "episodes": {}}

    async def _generate_bdmv(self, meta, filename, uuid, bdinfo, num_screens):  # TODO Perfect this. NN to test on TV BDMV
        # file = self._find_longest_file(bdinfo)
        # length = self._get_length(bdinfo)
        file, length = self.get_main_m2ts_and_duration(meta, uuid)
        keyframe = "nokey" if ("VC-1" in bdinfo["video"][0]["codec"] or bdinfo["video"][0]["hdr_dv"]) else "none"
        ss_times = [img.get("ss_time") for img in meta.get("image_list", {}).get("main", []) if "ss_time" in img]
        if not ss_times or (len(ss_times) < num_screens):
            ss_times = self._generate_valid_ss_times(length, num_screens)
        max_idx = max([int(re.search(r"\d+", Path(f).name).group()) for f in glob.glob(f"{filename}-*.png")] + [-1])
        tasks = [
            self._take_screenshot(file, f"{filename}-{max_idx + 1 + i}.png", t, keyframe, meta.get("ffdebug", False), hdr=meta["hdr"])
            for i, t in enumerate(ss_times)
        ]
        results = await asyncio.gather(*tasks)
        return await self._validate_and_retry(meta, results, file, filename, length, keyframe, num_screens)

    async def _generate_dvd(self, meta, filename, num_screens):
        disc_info = meta["discs"][0]
        vobs = [
            {"file": vob, "duration": float(ffmpeg.probe(vob)["format"]["duration"])}
            for vob in sorted(glob.glob(f"{disc_info['path']}/VTS_*_*.VOB"))
            if float(ffmpeg.probe(vob)["format"]["duration"]) >= 60
        ]
        if not vobs:
            raise ValueError("No valid VOBs found")

        total_length = sum(vob["duration"] for vob in vobs)
        screens_per_vob = max(1, num_screens // len(vobs))
        extra_screens = num_screens % len(vobs)
        max_idx = max([int(re.search(r"\d+", Path(f).name).group()) for f in glob.glob(f"{filename}-*.png")] + [-1])
        tasks = []
        for vob_idx, vob in enumerate(vobs):
            num = screens_per_vob + (1 if vob_idx < extra_screens else 0)
            ss_times = self._generate_valid_ss_times(vob["duration"], num)
            for i, t in enumerate(ss_times):
                tasks.append(
                    self._take_screenshot(
                        vob["file"],
                        f"{filename}-{max_idx + 1 + vob_idx * screens_per_vob + i}.png",
                        t,
                        "none",
                        meta.get("ffdebug", False),
                        vobs=vobs,
                    )
                )
        results = await asyncio.gather(*tasks)
        return await self._validate_and_retry(meta, results, vobs[0]["file"], filename, total_length, "none", num_screens, vobs)

    async def _generate_file(self, meta, filename, uuid, num_screens, file_path):
        if not Path(file_path).is_file():
            return []
        probe = ffmpeg.probe(file_path)
        length = float(probe["format"]["duration"])
        ss_times = [img.get("ss_time") for img in meta.get("image_list", {}).get("main", []) if "ss_time" in img]
        if not ss_times or (len(ss_times) < num_screens):
            ss_times = self._generate_valid_ss_times(length, num_screens)
        max_idx = max([int(re.search(r"\d+", Path(f).name).group()) for f in glob.glob(f"{filename}-*.png")] + [-1])
        tasks = [
            self._take_screenshot(
                file_path, f"{filename}-{max_idx + 1 + i}.png", t, "none", meta.get("ffdebug", False), hdr=meta.get("hdr")
            )
            for i, t in enumerate(ss_times)
        ]
        results = await asyncio.gather(*tasks)
        return await self._validate_and_retry(meta, results, file_path, filename, length, "none", num_screens)

    async def _take_screenshot(self, file, output, ss_time, keyframe, ffdebug, retries=3, vobs=None, hdr=None):
        loglevel = "verbose" if (ffdebug or log.ddebug) else "error"
        vf_filters = []
        if vobs:
            vf_filters.append(self.config["SCREENS"].get("deinterlace_method", "yadif"))
        if hdr and self.config["SCREENS"].get("tonemap_hdr", True):
            vf_filters.append("zscale=t=linear:npl=120,format=gbrpf32le,zscale=p=bt709")
            vf_filters.append(f"tonemap={self.config['SCREENS'].get('hdr_method', 'reinhard:desat=0')}")
            vf_filters.append("zscale=t=bt709:m=bt709:r=tv,format=rgb24")
        vf_filter = ",".join(vf_filters) or "scale=iw:ih"

        for attempt in range(retries):
            try:
                args = [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    loglevel,
                    "-ss",
                    str(ss_time),
                    "-i",
                    file,
                    "-vframes",
                    "1",
                    "-vf",
                    vf_filter,
                    output,
                ]
                log.ddebug(f" COMMAND CALLED:\n [blue]{args} \n")
                if keyframe == "nokey":
                    args.insert(1, "-skip_frame")
                    args.insert(2, "nokey")
                process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await process.communicate()
                if process.returncode == 0 and Path(output).exists():
                    size = Path(output).stat().st_size
                    if self.min_image_size <= size <= self.max_image_size and not self.is_monochromatic(output):
                        return (output, ss_time)
                    log.debug(f"Discarding {output}: {'monochromatic' if self.is_monochromatic(output) else 'invalid size'}")
                    Path(output).unlink()
            except Exception as e:
                log.error(f"FFmpeg failed for {output}: {e}")
                if Path(output).exists():
                    Path(output).unlink()
        return None

    async def _validate_and_retry(self, meta, results, file, filename, length, keyframe, num_screens, vobs=None):
        valid = [
            (r[0], r[1])
            for r in results
            if r and Path(r[0]).exists() and self.min_image_size <= Path(r[0]).stat().st_size <= self.max_image_size
        ]
        if len(valid) < num_screens:
            missing = num_screens - len(valid)
            new_times = self._generate_valid_ss_times(length, missing, avoid_times={t for _, t in valid})
            tasks = [
                self._take_screenshot(
                    file, f"{filename}-{i}.png", t, keyframe, meta.get("ffdebug", False), vobs=vobs, hdr=meta.get("hdr")
                )
                for i, t in enumerate(new_times)
            ]
            retry_results = await asyncio.gather(*tasks)
            valid.extend([r for r in retry_results if r and Path(r[0]).exists()])
        return valid[:num_screens]

    def _generate_valid_ss_times(self, length, num_screens, avoid_times=None):
        avoid_times = avoid_times or set()
        start_buffer = max(length * 0.1, 10)
        end_buffer = min(length * 0.9, length - 10)
        interval = (end_buffer - start_buffer) / max(num_screens, 1)
        times = set()
        for i in range(num_screens):
            while True:
                t = start_buffer + i * interval + random.uniform(-interval / 4, interval / 4)
                if t not in avoid_times and start_buffer <= t <= end_buffer:
                    times.add(t)
                    break
        return sorted(times)

    async def _generate_contact_sheet(self, screenshots, meta, output_path, rows, columns):
        thumbnail_width = 400
        thumbnail_height = int(thumbnail_width * (Image.open(screenshots[0][0]).size[1] / Image.open(screenshots[0][0]).size[0]))
        width = 2 * 3 + columns * thumbnail_width + (columns - 1) * 3
        height = 3 + 100 + rows * thumbnail_height + (rows - 1) * 3
        sheet = Image.new("RGB", (width, height), (0, 0, 0))
        for i, (path, _) in enumerate(screenshots[: rows * columns]):
            img = Image.open(path).resize((thumbnail_width, thumbnail_height), Image.Resampling.LANCZOS)
            x = 3 + (i % columns) * (thumbnail_width + 3)
            y = 103 + (i // columns) * (thumbnail_height + 3)
            sheet.paste(img, (x, y))
        sheet.save(output_path)

    def is_monochromatic(self, image_path, sample_size=100):
        try:
            with Image.open(image_path) as img:
                img = img.convert("RGB")
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

    def _is_sufficient(self, existing, meta, screens):
        if not existing:
            return False

        def files_exist(section):
            return all(
                (img.get("img_url") or (self.base_dir / "tmp" / meta["uuid"] / img.get("local_file", ""))).exists() for img in section
            )

        if meta.get("tv_pack") == 1:
            per_episode = self.config["SCREENS"].get("per_episode", 3)
            filelist = meta.get("filelist", [])
            if not filelist:
                return len(existing.get("main", [])) >= screens and files_exist(existing.get("main", []))
            return (
                len(existing.get("main", [])) >= screens
                and files_exist(existing.get("main", []))
                and all(
                    len(existing["episodes"].get(self._get_episode_identifier(path), [])) >= per_episode
                    and files_exist(existing["episodes"].get(self._get_episode_identifier(path), []))
                    for path in filelist
                    if self._is_video_file(path)
                )
            )

        return len(existing.get("main", [])) >= screens and files_exist(existing.get("main", []))

    def _find_largest_m2ts(self, bdinfo):
        stream_dir = Path(bdinfo["path"]) / "BDMV" / "STREAM"
        if not stream_dir.exists():
            raise FileNotFoundError(f"STREAM folder not found: {stream_dir}")

        m2ts_files = list(stream_dir.glob("*.m2ts"))
        if not m2ts_files:
            raise FileNotFoundError(f"No .m2ts files found in: {stream_dir}")

        largest_file = max(m2ts_files, key=lambda f: f.stat().st_size)
        return str(largest_file)

    def get_main_m2ts_and_duration(self, meta: dict, uuid: str) -> Tuple[str, float]:
        """Returns the path and duration (in seconds) of the largest .m2ts file in a BD structure."""
        mih = MediaInfoHandler()
        stream_path = Path(meta["path"]) / "BDMV" / "STREAM"

        if not stream_path.exists():
            raise FileNotFoundError(f"STREAM directory does not exist at {stream_path}")

        m2ts_files = list(stream_path.glob("*.m2ts"))
        if not m2ts_files:
            raise FileNotFoundError(f"No .m2ts files found in {stream_path}")

        largest = max(m2ts_files, key=lambda p: p.stat().st_size)
        cached = mih._cache.get(uuid)
        duration = None
        if cached:
            for track in cached.get("media", {}).get("track", []):
                if track.get("@type") == "Video" and "Duration" in track:
                    duration = float(track["Duration"])
                    break

        if duration is None:
            mi = MediaInfo.parse(str(largest))
            for track in mi.tracks:
                if track.track_type == "Video" and track.duration:
                    duration = float(track.duration)
                    break

        if duration is None or duration <= 0:
            raise ValueError(f"Could not extract valid duration from {largest}: duration={duration}")

        if duration < 60:
            raise ValueError(f"Duration {duration} seconds is too short for {largest}")
        log.debug(f"RETURNING LARGEST ({str(largest)}) + DURATION ({duration})")
        return str(largest), duration

    def _get_episode_identifier(self, path):
        match = re.search(r"S\d{2}E(\d{2})", str(path), re.IGNORECASE)
        return f"E{match.group(1)}" if match else Path(path).stem.split(".")[-1]

    def _is_video_file(self, path):
        return Path(path).suffix.lower() in {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm"}

    def _get_main_video_length(self, meta):
        return float(ffmpeg.probe(meta.get("path") or meta.get("filelist", [])[0])["format"]["duration"])

    def _get_rows_columns(self, length):
        if length <= 1500:
            return 3, 3  # noqa: E701
        elif length <= 4140:
            return 4, 3  # noqa: E701
        elif length <= 7200:
            return 5, 3  # noqa: E701
        elif length <= 10800:
            return 6, 3  # noqa: E701
        else:
            return 8, 3  # noqa: E701

    async def _optimize_image(self, image_path: str):
        """Run oxipng to losslessly optimize the PNG."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "oxipng", "-o4", "--strip", "all", image_path, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
        except Exception as e:
            log.debug(f"Failed to optimize {image_path}: {e}")

    @staticmethod
    def kill_orphaned_ffmpeg() -> None:
        for proc in psutil.process_iter(["pid", "name"]):
            if "ffmpeg" in proc.info["name"]:
                try:
                    proc.terminate()
                except Exception:
                    pass
