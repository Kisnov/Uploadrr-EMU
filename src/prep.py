import asyncio
from pathlib import Path
from typing import Dict

from src.mediainfo import MediaInfoHandler
from src.metascraper import MetaScraper
from src.musicprocessor import MusicProcessor
from src.name import NameGenerator
from src.progress import ProgressManager
from src.utils.console import console, log
from src.utils.descgen import DescGenerator
from src.videoprocessor import VideoProcessor


class Prep:
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
        self.mihandle = MediaInfoHandler(config, base_dir)
        self.desc_generator = DescGenerator(self.config, self.base_dir)

    async def gather_prep(self, meta, mode):  # TODO remove mode, that's discord relic (Double Check!)
        # Initialize gather
        meta.update(
            mode=mode,
            is_dir=Path(meta["path"]).is_dir(),
            is_music=False,
            is_video=False,
            filelist=[],
            user_images=[],
            log_files=[],
            nfo_file="",
        )

        # Create temp directory
        tmp_dir = self.base_dir / "tmp" / f"{meta['uuid']}"
        await asyncio.to_thread(tmp_dir.mkdir, parents=True, exist_ok=True)

        log.debug(f"[cyan]ID: {meta['uuid']}")

        # File collection and initial processing
        analyzer = FileAnalyzer()
        meta = await analyzer.analyze(meta, mode)

        if meta["is_music"]:
            # Process Music
            music_prep = MusicProcessor(self.mihandle, self.base_dir)
            meta = await music_prep.process_music(meta)
            meta = await self.desc_generator.gen_desc(meta)
        else:
            # Process Video
            console.print("[red]Processing as video")
            video_prep = VideoProcessor(self.config, self.mihandle, self.base_dir)
            meta = await video_prep.process_video(meta)
            meta = await self.desc_generator.gen_desc(meta)

        integrator = MetaScraper(self.config, pm=ProgressManager.get_instance())
        meta = await integrator.integrate(meta)

        # Create Name
        if not meta.get("is_sports"):
            meta["name_notag"], meta["name"], meta["clean_name"], meta["potential_missing"] = await NameGenerator().get_name(meta)

        return meta


class FileAnalyzer:
    """
    Analyzes a file or directory path, categorizes contents, and updates metadata.
    Handles both single files and directories recursively, with video priority.
    """

    # fmt: off
    VIDEO_EXTS = {'.mkv', '.mp4', '.ts', '.avi', '.wmv'}
    MUSIC_EXTS = {'.aac', '.ac3', '.alac', '.eac3', '.flac', '.m4a', '.mp3', '.ogg', '.ogm', '.oga', '.ogv', '.ogx', '.opus', '.spx', '.wav'}
    IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.svg', '.ico', '.gif'}
    LOG_EXT = '.log'
    NFO_EXT = '.nfo'
    # fmt:on
    def __init__(self):
        self.pm = ProgressManager.get_instance()

    async def analyze(self, meta: Dict, mode: str) -> Dict:
        """Analyzes path dictates process route."""
        path = Path(meta["path"])
        meta.update(
            is_dir=path.is_dir(),
            is_music=False,
            is_video=False,
            filelist=[],
            user_images=[],
            log_files=[],
            nfo_file="",
            content_size=0,
        )

        if self.pm:
            self.pm.update(f"Analyzing {path.name}...")

        if meta["is_dir"]:
            meta = await self._analyze_directory(meta, path, mode)
        else:
            meta = await self._analyze_file(meta, path, mode)

        if self.pm:
            self.pm.update(f"Analysis complete for {path.name}")

        return meta

    async def _analyze_directory(self, meta: Dict, path: Path, mode: str) -> Dict:
        """Async directory analysis."""
        all_files = []
        total_size = 0

        if self.pm:
            self.pm.update("Scanning directory...")

        async for file_path in self._async_iterdir(path):
            if file_path.is_file() and not file_path.name.startswith("."):
                all_files.append(str(file_path))
                total_size += file_path.stat().st_size
        if self.pm:
            self.pm.update(f"Scanned {len(all_files)} files in {path.name}")

        meta["content_size"] = total_size
        meta["filelist"] = sorted(all_files)  # Sort for consistency

        if self.pm:
            self.pm.update("Categorizing files...")

        # Categorize
        video_files = [f for f in all_files if Path(f).suffix.lower() in self.VIDEO_EXTS]
        music_files = [f for f in all_files if Path(f).suffix.lower() in self.MUSIC_EXTS]
        meta["user_images"] = [f for f in all_files if Path(f).suffix.lower() in self.IMAGE_EXTS]
        meta["log_files"] = [f for f in all_files if Path(f).suffix.lower() == self.LOG_EXT]
        nfo_files = [f for f in all_files if Path(f).suffix.lower() == self.NFO_EXT]
        base_name = path.name
        meta["nfo_file"] = next(
            (nfo for nfo in nfo_files if Path(nfo).name == f"{base_name}{self.NFO_EXT}"), nfo_files[0] if nfo_files else ""
        )

        # Prioritize video
        base_video = any(Path(f).parent == path for f in video_files)
        video_count = len(video_files)
        music_count = len(music_files)

        if video_count > 0 and (base_video or video_count > music_count * 2 or not meta.get("is_music")):
            meta["is_video"] = True
            meta["video_files"] = video_files
        elif music_count > 0:
            meta["is_music"] = True
            meta["music_files"] = music_files

        return meta

    async def _analyze_file(self, meta: Dict, path: Path, mode: str) -> Dict:
        """Async single file analysis."""
        if self.pm:
            self.pm.update(f"Analyzing file {path.name}...")

        ext = path.suffix.lower()
        try:
            meta["content_size"] = path.stat().st_size
        except (OSError, FileNotFoundError):
            meta["content_size"] = 0

        meta["filelist"] = [str(path)]

        if ext in self.VIDEO_EXTS:
            meta["is_video"] = True
            meta["video_files"] = [str(path)]
            base_name = path.stem
            nfo_path = path.with_name(f"{base_name}{self.NFO_EXT}")
            meta["nfo_file"] = str(nfo_path) if nfo_path.exists() else ""
            if not meta["nfo_file"] and meta.get("nfo"):
                nfo_files = [str(f) for f in path.parent.iterdir() if f.suffix.lower() == self.NFO_EXT]
                meta["nfo_file"] = nfo_files[0] if nfo_files else ""
        elif ext in self.MUSIC_EXTS and meta.get("is_music"):
            meta["is_music"] = True
            meta["music_files"] = [str(path)]

        return meta

    async def _async_iterdir(self, path: Path):
        """Async generator for recursive iterdir to allow concurrency if needed."""
        loop = asyncio.get_running_loop()
        for file_path in await loop.run_in_executor(None, list, path.rglob("*")):
            yield file_path
