import asyncio
import glob
import os
import random
import re
import sys
from pathlib import Path
from typing import Dict, Tuple

import ffmpeg
import psutil
from PIL import Image
from pymediainfo import MediaInfo

from src.mediainfo import MediaInfoHandler
from src.progress import ProgressManager
from src.utils.console import log

from .contact_sheet import ContactSheetGenerator

try:
    import oxipng

    OXIPNG_AVAILABLE = True
except ImportError:
    OXIPNG_AVAILABLE = False
    log.warning("pyoxipng not available. PNG optimization will use ffmpeg only.")


class ScreenshotGenerator:
    def __init__(self, config, base_dir):
        self.config = config
        self.base_dir = base_dir
        self.mihandle = MediaInfoHandler(config, base_dir)
        self.contact = ContactSheetGenerator(config, base_dir, self.mihandle)
        self.tmp_dir = None
        self.min_image_size = 75000
        self.cache = {}  # Simple in-memory cache: {uuid: List[Dict]}

        self.screens = self.config.get("SCREENS", {})
        self.use_oxipng = self.screens.get("use_oxipng", False) and OXIPNG_AVAILABLE
        self.pyoxipng_level = self.screens.get("oxipng_level", 4)  # Default level 4
        self.ffdebug = False

    async def generate_screenshots(self, meta: Dict, filename: str, uuid: str) -> Dict:
        """Generate screenshots for the given media, reusing existing ones if valid."""
        pm = ProgressManager.get_instance()
        if pm:
            pm.update(f"Starting screenshot generation for {meta.get('name', filename)}")

        self.ffdebug = meta.get("ffdebug", False) or meta.get("deep_debug")

        screens = meta.get("screens", self.config.get("DEFAULT", {}).get("screens", 3))
        contact_screens = 0
        rows = columns = None
        if meta.get("xxx", False):
            contact_cfg = self.config.get("ADULT", {}).get("contact_sheet", {})
            rows = contact_cfg.get("row")
            columns = contact_cfg.get("column")
            if rows is not None and columns is not None:
                contact_screens = rows * columns
            else:
                length = self._get_length(meta)
                r, c = self._get_rows_columns(length)
                contact_screens = r * c

        self.tmp_dir = tmp_dir = self.base_dir / "tmp" / uuid
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

        if pm:
            pm.update("Screenshot generation complete")

        # -- CONTACT SHEETS -- #
        if contact_screens > 0:
            if pm:
                pm.update("Creating contact sheet")

            video_files = meta.get("video_files", [meta.get("video")])
            max_sheets = contact_cfg.get("max", 1) if not meta.get("xmovie") else len(video_files)
            num_sheets = min(len(video_files), max_sheets)

            result["contact_sheets"] = []
            if meta.get("xmovie"):
                for idx, vf in enumerate(video_files[:num_sheets]):
                    if pm:
                        pm.update(f"Generating screenshots for contact sheet {idx + 1}")

                    scene_filename = f"{filename}-scene_{idx + 1}"
                    length = float(ffmpeg.probe(vf)["format"]["duration"])
                    rows = rows if rows is not None else self._get_rows_columns(length)[0]
                    columns = columns if columns is not None else self._get_rows_columns(length)[1]
                    num_needed = rows * columns
                    file_screenshots = await self._generate_file_screenshots(
                        meta, scene_filename, uuid, screens=num_needed, contact_screens=0, video_path=vf
                    )
                    screenshots = file_screenshots["main"]

                    meta_with_cache = meta.copy()
                    meta_with_cache.pop("cached_screenshots", None)

                    out_path = tmp_dir / f"contact_sheet_{idx + 1}.png"
                    await self.contact.generate_contact_sheet(screenshots, meta_with_cache, out_path, rows, columns, video_file=vf)
                    result["contact_sheets"].append({"local_file": out_path.name})

                    # Cleanup extra screenshots for this contact sheet
                    for ss in screenshots:
                        (tmp_dir / ss["local_file"]).unlink(missing_ok=True)
            else:
                length = self._get_main_video_length(meta)
                rows = rows if rows is not None else self._get_rows_columns(length)[0]
                columns = columns if columns is not None else self._get_rows_columns(length)[1]

                meta_with_cache = meta.copy()
                meta_with_cache["cached_screenshots"] = self.cache.get(uuid, [])

                out_path = tmp_dir / "contact_sheet.png"
                await self.contact.generate_contact_sheet(result["main"], meta_with_cache, out_path, rows, columns)
                result["contact_sheets"].append({"local_file": out_path.name})

                # Cleanup extra screenshots used only for contact sheet
                if contact_screens > screens:
                    all_screenshots_files = sorted(
                        tmp_dir.glob(f"{filename}-*.png"), key=lambda p: int(re.search(r"-(\d+)\.png$", p.name).group(1))
                    )
                    extra_files = all_screenshots_files[screens:]
                    for ef in extra_files:
                        ef.unlink(missing_ok=True)

        # -- OPTIMIZATION with oxipng -- #
        if self.use_oxipng and OXIPNG_AVAILABLE:
            if pm:
                pm.update("Optimizing with oxipng")
            optimize_tasks = [self._optimize_image((tmp_dir / img["local_file"])) for img in result["main"]]
            await asyncio.gather(*optimize_tasks)
            if pm:
                pm.update("Optimization Complete")

        if uuid in self.cache:
            del self.cache[uuid]
        self.cache.clear()

        return result

    async def _generate_file_screenshots(self, meta, filename, uuid, screens, contact_screens, video_path=None):
        """Generate screenshots for a single file, reusing valid existing ones."""
        if video_path is None:
            video_path = meta.get("video")
        num_needed = max(screens, contact_screens)

        pattern = f"{filename}-*.png"
        existing_screenshots = [
            str(p) for p in self.tmp_dir.glob(pattern) if p.stat().st_size > 1000 and not self.is_monochromatic(str(p))
        ]

        num_existing = len(existing_screenshots)
        num_to_generate = num_needed - num_existing
        ss_time_map = {img["local_file"]: img.get("ss_time", 0) for img in meta.get("image_list", {}).get("main", [])}

        if num_to_generate > 0:
            new_screenshots = await self._generate_file(meta, filename, uuid, num_to_generate, video_path)
            existing_pairs = [(img, ss_time_map.get(Path(img).name, 0)) for img in existing_screenshots]
            combined = existing_pairs + new_screenshots
            all_screenshots = [{"local_file": Path(path).name, "ss_time": t} for path, t in combined]
        else:
            all_screenshots = [
                {"local_file": Path(img).name, "ss_time": ss_time_map.get(Path(img).name, 0)} for img in existing_screenshots
            ]

        self.cache[uuid] = all_screenshots
        return {"main": all_screenshots[:screens], "episodes": {}}

    async def _generate_tv_pack_screenshots(self, meta, filename, uuid, screens, contact_screens):
        """Generate screenshots for TV pack, reusing valid existing ones per episode."""
        per_episode = self.screens.get("per_episode", 3)
        filelist = sorted(meta.get("filelist", []), key=self._get_episode_identifier)
        result = {"main": [], "episodes": {}}

        ss_time_map_main = {img["local_file"]: img.get("ss_time", 0) for img in meta.get("image_list", {}).get("main", [])}
        episodes_images = meta.get("image_list", {}).get("episodes", {})

        for ep_path in filelist:
            if not self._is_video_file(ep_path):
                continue
            ep_name = self._get_episode_identifier(ep_path)
            ep_filename = f"{filename}-{ep_name}"
            num_needed = max(screens, contact_screens) if ep_name == "E01" else per_episode

            # -- VALIDATION -- #
            pattern = f"{ep_filename}-*.png"
            existing_screenshots = [
                str(p) for p in self.tmp_dir.glob(pattern) if p.stat().st_size > 1000 and not self.is_monochromatic(str(p))
            ]
            num_existing = len(existing_screenshots)
            num_to_generate = num_needed - num_existing
            ep_ss_map = (
                {img["local_file"]: img.get("ss_time", 0) for img in episodes_images.get(ep_name, [])} if episodes_images else {}
            )

            if num_to_generate > 0:
                new_screenshots = await self._generate_file(meta, ep_filename, uuid, num_to_generate, ep_path)
                existing_pairs = [
                    (img, ep_ss_map.get(Path(img).name, ss_time_map_main.get(Path(img).name, 0))) for img in existing_screenshots
                ]
                combined = existing_pairs + new_screenshots
                ep_screens = [{"local_file": Path(path).name, "ss_time": t} for path, t in combined]
            else:
                ep_screens = [
                    {"local_file": Path(img).name, "ss_time": ep_ss_map.get(Path(img).name, ss_time_map_main.get(Path(img).name, 0))}
                    for img in existing_screenshots
                ]

            if ep_name == "E01":
                result["main"] = ep_screens[:screens]
            result["episodes"][ep_name] = ep_screens[:num_needed]

        return result

    async def _generate_bdmv_screenshots(self, meta, filename, uuid, screens, contact_screens):
        """Generate screenshots for BDMV, reusing valid existing ones."""
        num_needed = max(screens, contact_screens)

        # -- VALIDATION -- #
        pattern = f"{filename}-*.png"
        existing_screenshots = [
            str(p) for p in self.tmp_dir.glob(pattern) if p.stat().st_size > 1000 and not self.is_monochromatic(str(p))
        ]

        num_existing = len(existing_screenshots)
        num_to_generate = num_needed - num_existing
        ss_time_map = {img["local_file"]: img.get("ss_time", 0) for img in meta.get("image_list", {}).get("main", [])}

        if num_to_generate > 0:
            bdinfo = meta["bdinfo"]
            new_screenshots = await self._generate_bdmv(meta, filename, uuid, bdinfo, num_to_generate)
            existing_pairs = [(img, ss_time_map.get(Path(img).name, 0)) for img in existing_screenshots]
            combined = existing_pairs + new_screenshots
            all_screenshots = [{"local_file": Path(path).name, "ss_time": t} for path, t in combined]
        else:
            all_screenshots = [
                {"local_file": Path(img).name, "ss_time": ss_time_map.get(Path(img).name, 0)} for img in existing_screenshots
            ]

        self.cache[uuid] = all_screenshots
        return {"main": all_screenshots[:screens], "episodes": {}}

    async def _generate_dvd_screenshots(self, meta, filename, uuid, screens, contact_screens):
        """Generate screenshots for DVD, reusing valid existing ones."""
        num_needed = max(screens, contact_screens)

        # -- VALIDATION -- #
        pattern = f"{filename}-*.png"
        existing_screenshots = [
            str(p) for p in self.tmp_dir.glob(pattern) if p.stat().st_size > 1000 and not self.is_monochromatic(str(p))
        ]

        num_existing = len(existing_screenshots)
        num_to_generate = num_needed - num_existing
        ss_time_map = {img["local_file"]: img.get("ss_time", 0) for img in meta.get("image_list", {}).get("main", [])}

        if num_to_generate > 0:
            new_screenshots = await self._generate_dvd(meta, filename, num_to_generate)
            existing_pairs = [(img, ss_time_map.get(Path(img).name, 0)) for img in existing_screenshots]
            combined = existing_pairs + new_screenshots
            all_screenshots = [{"local_file": Path(path).name, "ss_time": t} for path, t in combined]
        else:
            all_screenshots = [
                {"local_file": Path(img).name, "ss_time": ss_time_map.get(Path(img).name, 0)} for img in existing_screenshots
            ]

        self.cache[uuid] = all_screenshots
        return {"main": all_screenshots[:screens], "episodes": {}}

    async def _generate_bdmv(self, meta, filename, uuid, bdinfo, num_screens):  # TODO Perfect this. NN to test on TV BDMV
        file, length = self.get_main_m2ts_and_duration(meta, uuid)
        keyframe = "nokey" if ("VC-1" in bdinfo["video"][0]["codec"] or bdinfo["video"][0]["hdr_dv"]) else "none"
        ss_times = [img.get("ss_time") for img in meta.get("image_list", {}).get("main", []) if "ss_time" in img]
        if not ss_times or (len(ss_times) < num_screens):
            ss_times = self._generate_valid_ss_times(length, num_screens)
        png_files = self.tmp_dir.glob(f"{filename}-*.png")
        indices = []
        for p in png_files:
            match = re.search(r"\d+", p.name)
            if match:
                indices.append(int(match.group()))
        max_idx = max(indices + [-1])
        tasks = [
            self._take_screenshot(file, f"{filename}-{max_idx + 1 + i}.png", t, keyframe, self.ffdebug, hdr=meta["hdr"])
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
        indices = [int(m.group()) for p in self.tmp_dir.glob(f"{filename}-*.png") if (m := re.search(r"\d+", p.name))]
        max_idx = max(indices + [-1])
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
                        self.ffdebug,
                        vobs=vobs,
                    )
                )
        results = await asyncio.gather(*tasks)
        return await self._validate_and_retry(meta, results, vobs[0]["file"], filename, total_length, "none", num_screens, vobs)

    async def _generate_file(self, meta, filename, uuid, num_screens, file_path):
        if not Path(file_path).is_file():
            return []
        keyframe = "none"
        try:
            probe = await self._ffprobe(
                file_path, "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", ffdebug=self.ffdebug
            )
            format_info = await self._ffprobe(file_path, "-show_format", ffdebug=self.ffdebug)

            length = float(format_info["format"]["duration"])
            frame_rate_str = probe["streams"][0]["r_frame_rate"]
            num, denom = map(int, frame_rate_str.split("/"))
            frame_rate = num / denom

        except (KeyError, ValueError, RuntimeError) as e:
            try:
                probe = await self._ffprobe(file_path, "-show_format", ffdebug=self.ffdebug)
                length = float(probe["format"]["duration"])
                frame_rate = 23.976  # Fallback
                log.warning(f"Failed to probe frame rate for {file_path}: {e}")
            except (KeyError, ValueError, RuntimeError) as e2:
                log.error(f"Failed to probe file {file_path}: {e2}")
                raise RuntimeError(f"Failed to probe file {file_path}") from e2

        ss_times = [
            img.get("ss_time") for img in meta.get("image_list", {}).get("main", []) if img.get("ss_time") and img.get("ss_time") != 0
        ]
        if not ss_times or (len(ss_times) < num_screens):
            ss_times = self._generate_valid_ss_times(length, num_screens)

        keyframes = []
        if self.screens.get("keyframes_only", False) and self.screens.get(
            "frame_stats", False
        ):  # only needs adjust if requesting stats
            try:
                # ±30 seconds to ensure keyframes are captured
                intervals = ",".join(f"{max(0, t - 30)}%{t + 30}" for t in ss_times)
                keyframe_probe = await self._ffprobe(
                    file_path,
                    "-select_streams",
                    "v:0",
                    "-skip_frame",
                    "nokey",  # Only show keyframes (I-frames)
                    "-show_entries",
                    "frame=pkt_pts_time,key_frame",
                    "-show_frames",
                    "-read_intervals",
                    intervals,
                    ffdebug=self.ffdebug,
                )
                if self.ffdebug:
                    log.info(f"Raw ffprobe output for keyframes: {keyframe_probe}")
                keyframes = []
                for frame in keyframe_probe.get("frames", []):
                    if frame.get("key_frame") == 1:
                        try:
                            keyframes.append(float(frame["pts_time"]))
                        except (ValueError, KeyError):
                            continue  # Skip malformed frames
                if not keyframes:
                    log.warning(f"No keyframes found for {file_path} in intervals {intervals}. Using original ss_times.")
                else:
                    log.debug(f"Found {len(keyframes)} keyframes for {file_path}")
            except Exception as e:
                error_msg = f"Failed to probe keyframes for {file_path}: {e}"
                log.warning(error_msg)
                keyframes = []

        adjusted_ss_times = []
        if keyframes:
            keyframe = "nokey"
            for t in ss_times:
                closest_keyframe = min(keyframes, key=lambda x: abs(x - t))
                adjusted_ss_times.append(closest_keyframe)
                log.debug(f"Adjusted ss_time {t} to keyframe {closest_keyframe}")
        else:
            adjusted_ss_times = ss_times

        indices = [int(m.group()) for p in self.tmp_dir.glob(f"{filename}-*.png") if (m := re.search(r"\d+", p.name))]
        max_idx = max(indices + [-1])
        tasks = [
            self._take_screenshot(
                file_path,
                f"{filename}-{max_idx + 1 + i}.png",
                t,
                keyframe,
                self.ffdebug,
                hdr=meta.get("hdr"),
                frame_rate=frame_rate,
                adjusted_ss_time=t,
            )
            for i, t in enumerate(adjusted_ss_times)
        ]
        results = await asyncio.gather(*tasks)
        return await self._validate_and_retry(meta, results, file_path, filename, length, "none", num_screens)

    async def _take_screenshot(
        self, file, output, ss_time, keyframe, ffdebug, retries=3, vobs=None, hdr=None, frame_rate=23.976, adjusted_ss_time=None
    ):
        loglevel = "verbose" if ffdebug else "error"
        vf_filters = []
        if vobs:
            vf_filters.append(self.screens.get("deinterlace_method", "yadif"))
        if hdr == "DV" and self.screens.get("tonemap_hdr", True):
            # Dolby Vision sin capa HDR10 (perfil 5): no trae etiquetas de color y zscale falla; libplacebo aplica el RPU.
            # contrast_recovery=0: con Vulkan por CPU deja cuadrículas que cambian en cada pasada.
            vf_filters.append(
                "libplacebo=tonemapping=hable:contrast_recovery=0:colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
            )
            vf_filters.append("format=rgb24")
        elif hdr and self.screens.get("tonemap_hdr", True):
            vf_filters.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=709")
            vf_filters.append(f"tonemap={self.config['SCREENS'].get('hdr_method', 'reinhard:desat=0')}")
            vf_filters.append("zscale=t=bt709:m=bt709:r=full,format=rgb24")
        if self.screens.get("frame_stats", False):
            font = str(Path(__file__).parent / "fonts" / "Dosis.ttf")
            if sys.platform == "win32":
                font = font.replace("\\", "\\\\").replace(":", "\\:")
            frame_time = adjusted_ss_time if adjusted_ss_time is not None else ss_time
            frame_num = int(round(frame_time * frame_rate))
            stats = (
                "drawtext=fontfile='{}':"
                "fontsize=24:fontcolor=white:x=10:y=10:box=1:boxcolor=black@0.5:boxborderw=5:"
                "text='%{{pict_type}}-Frame \nFrame\\: {}  \n%{{pts\\:hms}}'"
            ).format(font, frame_num)
            vf_filters.append(stats)

        vf_filter = ",".join(vf_filters) or "scale=iw:ih"

        optimize = self.screens.get("optimize", False)
        if not self.use_oxipng and optimize:
            o_lvl = 6 if optimize is True else min(optimize, 9)
        else:
            o_lvl = 0

        for attempt in range(retries):
            try:
                args = [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    loglevel,
                    "-hide_banner",
                    "-ss",
                    str(ss_time),
                    "-copyts",
                    "-i",
                    file,
                    "-vframes",
                    "1",
                    "-vf",
                    vf_filter,
                    "-compression_level",
                    str(o_lvl),
                    "-pred",
                    "mixed",
                    output,
                ]

                if keyframe == "nokey":
                    args.insert(1, "-skip_frame")
                    args.insert(2, "nokey")

                if vf_filter.startswith("libplacebo"):
                    args[1:1] = ["-init_hw_device", "vulkan"]

                if ffdebug:
                    log.info(f" COMMAND CALLED:\n [blue]{args} \n")

                process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await process.communicate()
                stderr_text = stderr.decode("utf-8", errors="ignore")
                if ffdebug:
                    log.info(f"ffmpeg stderr:\n{stderr_text}")

                if process.returncode != 0:
                    log.error(f"FFmpeg failed for {output} with returncode {process.returncode}: {stderr.decode()}")
                    continue

                if process.returncode == 0 and Path(output).exists():
                    size = Path(output).stat().st_size
                    if self.min_image_size <= size and not self.is_monochromatic(output):
                        return (output, ss_time)
                    log.debug(f"Discarding {output}: {'monochromatic' if self.is_monochromatic(output) else 'invalid size'}")
                    Path(output).unlink()

            except Exception as e:
                log.error(f"FFmpeg failed for {output}: {e}")
                if Path(output).exists():
                    Path(output).unlink()

        return None

    async def _validate_and_retry(self, meta, results, file, filename, length, keyframe, num_screens, vobs=None):
        """Validate screenshots and retry missing ones"""

        valid = [
            (r[0], r[1])
            for r in results
            if r and Path(r[0]).exists() and self.min_image_size <= Path(r[0]).stat().st_size and not self.is_monochromatic(r[0])
        ]

        missing = num_screens - len(valid)
        if missing > 0:
            used_times = {t for _, t in valid}
            new_times = self._generate_valid_ss_times(length, missing, avoid_times=used_times)
            start_idx = len(valid) + 1
            tasks = [
                self._take_screenshot(
                    file, f"{filename}-{start_idx + i}.png", t, keyframe, self.ffdebug, vobs=vobs, hdr=meta.get("hdr")
                )
                for i, t in enumerate(new_times)
            ]
            retry_results = await asyncio.gather(*tasks)
            valid.extend([
                r
                for r in retry_results
                if r and Path(r[0]).exists() and self.min_image_size <= Path(r[0]).stat().st_size and not self.is_monochromatic(r[0])
            ])
        return valid

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
            tmp_path = self.base_dir / "tmp" / meta["uuid"]
            for img in section:
                img_url = img.get("img_url")
                if img_url and img_url.strip():
                    continue
                local_file = img.get("local_file", "")
                if local_file and (tmp_path / local_file).exists():
                    continue
                return False
            return True

        if meta.get("tv_pack") == 1:
            per_episode = self.screens.get("per_episode", 3)
            filelist = meta.get("filelist", [])
            if not filelist:
                return len(existing.get("main", [])) >= screens and files_exist(existing.get("main", []))
            return (
                len(existing.get("main", [])) >= screens
                and files_exist(existing.get("main", []))
                and all(
                    (
                        len(existing["episodes"].get(self._get_episode_identifier(path), [])) >= per_episode
                        and files_exist(existing["episodes"].get(self._get_episode_identifier(path), []))
                    )
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
        stream_path = Path(meta["path"]) / "BDMV" / "STREAM"

        if not stream_path.exists():
            raise FileNotFoundError(f"STREAM directory does not exist at {stream_path}")

        m2ts_files = list(stream_path.glob("*.m2ts"))
        if not m2ts_files:
            raise FileNotFoundError(f"No .m2ts files found in {stream_path}")

        largest = max(m2ts_files, key=lambda p: p.stat().st_size)
        cached = self.mihandle._cache.get(uuid)
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
        video_files = meta.get("video_files", [meta.get("video")])
        return float(ffmpeg.probe(video_files[0])["format"]["duration"])

    def _get_length(self, meta):
        try:
            if meta.get("bdinfo") not in (None, ""):
                _, duration = self.get_main_m2ts_and_duration(meta, meta["uuid"])
                return duration
            elif meta.get("discs"):
                disc_info = meta["discs"][0]
                vobs = [
                    vob
                    for vob in sorted(glob.glob(f"{disc_info['path']}/VTS_*_*.VOB"))
                    if float(ffmpeg.probe(vob)["format"]["duration"]) >= 60
                ]
                return sum(float(ffmpeg.probe(vob)["format"]["duration"]) for vob in vobs)
            elif meta.get("tv_pack") == 1:
                filelist = sorted(meta.get("filelist", []), key=self._get_episode_identifier)
                first_ep = next((p for p in filelist if self._is_video_file(p)), None)
                if first_ep:
                    return float(ffmpeg.probe(first_ep)["format"]["duration"])
                return 0
            else:
                video = meta.get("video", meta.get("video_files", [None])[0])
                if video and Path(video).is_file():
                    return float(ffmpeg.probe(video)["format"]["duration"])
                return 0
        except Exception as e:
            log.warning(f"Failed to get length for contact sheet: {e}")
            return 7200  # default assume medium

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

    async def _ffprobe(self, file_path: str, *extra_args, ffdebug=False) -> dict:
        """Run ffprobe asynchronously and return parsed JSON output."""
        import json

        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", *extra_args, str(file_path)]  # Use str(file_path) for safety
        if ffdebug:
            log.info(f"Running ffprobe: {' '.join(cmd)!r}")
        try:
            process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)  # 5-minute timeout to prevent hangs
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()  # Wait for kill to complete
                raise RuntimeError(f"ffprobe timed out for {file_path}")

            stderr_text = stderr.decode("utf-8", errors="ignore").strip()
            if process.returncode != 0:
                raise RuntimeError(f"ffprobe failed with code {process.returncode}: {stderr_text}")

            if not stdout:
                raise RuntimeError("ffprobe returned empty output")

            return json.loads(stdout.decode("utf-8"))

        except json.JSONDecodeError as e:
            raise RuntimeError(f"ffprobe returned invalid JSON: {e}") from e
        except Exception as e:
            raise RuntimeError(f"ffprobe error: {str(e)}") from e

    async def _optimize_image(self, image_path: str):
        """Run oxipng to losslessly optimize the PNG."""
        try:
            oxipng.optimize(image_path, level=self.pyoxipng_level)
            log.debug(f"Optimized {image_path} with pyoxipng level {self.pyoxipng_level}")
        except Exception as e:
            log.warning(f"Failed to optimize {image_path} with pyoxipng: {e}")

    @staticmethod
    def kill_orphaned_ffmpeg() -> None:
        for proc in psutil.process_iter(["pid", "name"]):
            if "ffmpeg" in proc.info["name"]:
                try:
                    proc.terminate()
                except Exception:
                    pass
