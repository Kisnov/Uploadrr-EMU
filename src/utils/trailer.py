import asyncio
import random
import re
from pathlib import Path
from typing import Dict, Optional

from src.progress import ProgressManager
from src.utils.console import log


async def _run_ffmpeg(cmd: list[str], desc: str) -> tuple[int, str, str]:
    """Run ffmpeg asynchronously, streaming output and updating progress."""
    progress = ProgressManager.get_instance()
    if progress:
        progress.update(f"{desc}…")

    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()

    stdout_str = stdout.decode(errors="ignore")
    stderr_str = stderr.decode(errors="ignore")

    if progress:
        progress.update(f"{desc} – {'Done' if proc.returncode == 0 else 'Failed'}")

    return proc.returncode, stdout_str, stderr_str


async def trailer_to_webp(video_path: Path, output_dir: Path, config: Dict) -> Optional[Path]:
    """
    Convert a downloaded trailer to an animated WebP.

    Config keys (all under config['ADULT']['trailer']):
        - fps: int | None      → frames per second (default 20)
        - maxwidth: int | None → max width in pixels (default 800)
        - crop: bool | None    → enable cropdetect (default True)
        - sample: str | None   → time to sample for cropdetect (default "1")
        - frames: int | None   → # of frames to analyze for crop (default 12)
        - scale: float | None  → base scaling before maxwidth (default 1.0)

    Returns Path to trailer.webp or None on failure.
    """
    trailer_cfg = config.get("ADULT", {}).get("trailer", {})

    fps = int(trailer_cfg.get("fps") or 20)
    max_width = int(trailer_cfg.get("maxwidth") or 800)
    do_crop = trailer_cfg.get("crop", True) is not False
    sample_time = str(trailer_cfg.get("sample") or "1")
    crop_frames = int(trailer_cfg.get("frames") or 12)
    base_scale = float(trailer_cfg.get("scale") or 1.0)

    output_path = output_dir / "trailer.webp"

    # Crop Detect
    crop_filter = ""
    if do_crop:
        crop_cmd = [
            "ffmpeg",
            "-ss",
            sample_time,
            "-i",
            str(video_path),
            "-vframes",
            str(crop_frames),
            "-vf",
            "cropdetect=24:16:0",
            "-f",
            "null",
            "-",
        ]
        ret, _, stderr = await _run_ffmpeg(crop_cmd, "Detecting crop")
        if ret != 0:
            log.warning("cropdetect failed – proceeding without cropping")
        else:
            m = re.search(r"crop=(\d+:\d+:\d+:\d+)", stderr)
            if m:
                crop_raw = m.group(1)
                w, h = map(int, crop_raw.split(":")[:2])
                log.debug(f"Detected crop: {crop_raw} ({w}x{h})")
            else:
                log.warning("cropdetect found no crop – using full frame")
                w = h = 0  # will skip scaling based on crop
    else:
        w = h = 0  # force full-frame path

    # Final Dimensions
    if w and h:
        new_w = int(w * base_scale)
        new_h = int(h * base_scale)
    else:
        # Fallback: probe video resolution
        probe_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(video_path),
        ]
        ret, stdout, _ = await _run_ffmpeg(probe_cmd, "Probing resolution")
        if ret == 0 and "x" in stdout.strip():
            new_w, new_h = map(int, stdout.strip().split("x"))
            new_w = int(new_w * base_scale)
            new_h = int(new_h * base_scale)
        else:
            log.error("Failed to probe video size – aborting WebP conversion")
            return None

    # Enforce max width
    if new_w > max_width:
        ratio = max_width / new_w
        new_w = int(new_w * ratio)
        new_h = int(new_h * ratio)

    # Even dimensions required by libwebp
    new_w = new_w - (new_w % 2)
    new_h = new_h - (new_h % 2)
    if new_w < 2:
        new_w = 2
    if new_h < 2:
        new_h = 2

    log.debug(f"Final WebP size: {new_w}x{new_h}, FPS: {fps}")

    vf = []
    if do_crop and crop_filter:
        vf.append(f"crop={crop_raw}")
    vf.append(f"scale={new_w}:{new_h}")
    vf.append(f"fps={fps}")

    encode_cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-an",
        "-vf",
        ",".join(vf),
        "-loop",
        "0",
        "-c:v",
        "libwebp",
        "-quality",
        "80",
        "-compression_level",
        "6",
        "-y",
        str(output_path),
    ]

    ret, _, stderr = await _run_ffmpeg(encode_cmd, "Encoding WebP")
    if ret != 0:
        log.error(f"WebP encoding failed: {stderr.splitlines()[-3:]}")
        return None

    log.info(f"Trailer WebP saved: {output_path}")
    return str(output_path)


async def create_trailer(video_path: Path, output_dir: Path, config: Dict) -> Optional[Path]:
    """
    Create a 20-second trailer by taking 4 random 5-second sections from the main video.
    Avoids first and last 20 seconds to skip pre/end credits.

    Returns Path to trailer.webp or None on failure.
    """
    # Get video duration
    probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)]
    ret, stdout, _ = await _run_ffmpeg(probe_cmd, "Probing video duration")
    if ret != 0:
        log.error("Failed to get video duration")
        return None

    try:
        duration = float(stdout.strip())
    except ValueError:
        log.error(f"Could not parse video duration: {stdout.strip()}")
        return None

    if duration <= 40:
        log.warning(f"Video too short ({duration}s) to create trailer with credit avoidance")
        return None

    # Avoid pre/end credits
    start_range = 20
    end_range = duration - 20 - 5

    if start_range >= end_range:
        log.warning("Video too short to safely avoid credits")
        return None

    # Select 4 random start times for 5-second segments
    selected_times = []
    attempts = 0
    max_attempts = 50  # Prevent infinite loop if conditions are impossible

    while len(selected_times) < 4 and attempts < max_attempts:
        start_time = random.uniform(start_range, end_range)
        if all(abs(start_time - existing_time) >= 6 for existing_time in selected_times):
            selected_times.append(start_time)
        attempts += 1

    if len(selected_times) < 4:
        log.warning(f"Could only select {len(selected_times)} segments, expected 4")
        if len(selected_times) == 0:
            return None

    selected_times.sort()

    # Create temporary files for each segment
    temp_segments = []
    for i, start_time in enumerate(selected_times):
        temp_path = output_dir / f"temp_segment_{i}.mp4"
        temp_segments.append(temp_path)

        segment_cmd = ["ffmpeg", "-ss", str(start_time), "-t", "5", "-i", str(video_path), "-c", "copy", "-y", str(temp_path)]

        ret, _, stderr = await _run_ffmpeg(segment_cmd, f"Creating segment {i + 1}/4")
        if ret != 0:
            log.error(f"Failed to create segment {i + 1}: {stderr}")
            for temp_seg in temp_segments:
                if temp_seg.exists():
                    temp_seg.unlink()
            return None

    concat_file = output_dir / "concat_list.txt"
    try:
        with open(concat_file, "w") as f:
            for temp_path in temp_segments:
                f.write(f"file '{temp_path.absolute()}'\n")

        concatenated = output_dir / "temp_concatenated.mp4"
        concat_cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-y", str(concatenated)]

        ret, _, stderr = await _run_ffmpeg(concat_cmd, "Concatenating segments")
        if ret != 0:
            log.error(f"Failed to concatenate segments: {stderr}")
            return None

        webp_path = await trailer_to_webp(concatenated, output_dir, config)

        # Clean up
        if concatenated.exists():
            concatenated.unlink()
        for temp_path in temp_segments:
            if temp_path.exists():
                temp_path.unlink()
        if concat_file.exists():
            concat_file.unlink()

        return str(webp_path)

    except Exception as e:
        log.error(f"Error in trailer creation: {e}")
        # Clean up on error
        for temp_path in temp_segments:
            if temp_path.exists():
                temp_path.unlink()
        if concat_file.exists():
            concat_file.unlink()
        concatenated = output_dir / "temp_concatenated.mp4"
        if concatenated.exists():
            concatenated.unlink()
        return None
