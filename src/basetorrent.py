import asyncio
import glob
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from rich.progress import BarColumn, Progress, SpinnerColumn, TimeRemainingColumn
from torf import Torrent

from src.console import console, log

class TorrentMaker:
    def __init__(self, config):
        self.config = config
        self.tracker = 'https://fake.tracker'

    async def create(self, meta: dict, path: Path, piece_size_max: Optional[int] = None) -> Torrent:
        """Create a new torrent file with consistent content rules across tools."""
        try:
            piece_size_max = int(piece_size_max) if piece_size_max is not None else 0
            path = self._prepare_path(meta, path)
            exclude, include = self._get_filters(meta)
            
            torrent = self._initialize(path, exclude, include)
            piece_size, piece_size_text = self._calculate_piece_size(torrent.size, piece_size_max)
            
            console.print(f"[bold yellow]Creating .torrent with piece size {piece_size_text}...")
            
            torrent_creation = meta.get('torrent_creation')
            log.debug(f"Tool: {torrent_creation}")
            output_path = Path(meta['base_dir']) / "tmp" / meta['uuid'] / "BASE.torrent"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            log.debug(f"Path used for torrent: {path}")
            log.debug(f"Exclude patterns: {exclude}")
            log.debug(f"Include patterns: {include}")
            log.debug(f"Output path: {output_path}")
            log.debug(f"Using method: {torrent_creation}")


            await self._generate(torrent, path, output_path, piece_size, torrent_creation)
            
            console.print("[bold green].torrent created", end="\r")
            return torrent
        except ValueError as e:
            log.error(f"Invalid input: {e}")
            raise
        except Exception as e:
            log.error(f"Torrent creation failed: {e}")
            raise

    def _prepare_path(self, meta: dict, path: Path) -> str:
        """Prepare the path based on metadata conditions."""
        if not meta.get('full_dir') and meta.get('is_dir'):
            os.chdir(path)
            valid_extensions = {".mkv", ".mp4", ".ts"}
            files = [
                f for f in Path(path).iterdir()
                if f.suffix.lower() in valid_extensions and "sample" not in f.name.lower()
            ]
            if len(files) == 1:
                return meta['filelist'][0]
        return str(path)

    def _get_filters(self, meta: dict) -> Tuple[list, list]:
        """Determine include/exclude filters based on metadata."""
        if any(meta.get(k) for k in ['full_dir', 'is_disc', 'is_music']):
            desc = meta['uuid'] if meta.get('is_dir', False) else Path(meta['uuid']).stem
            return ['._*', 'description.txt', f'{desc}.txt'], []
        return ["*.*", "*sample.mkv", "!sample*.*", "._*"], ["*.mkv", "*.mp4", "*.ts"]

    def _initialize(self, path: Path, exclude: list, include: list) -> Torrent:
        """Initialize torrent object with basic settings."""
        return Torrent(
            path=path,
            trackers=self.tracker,
            source="Uploadrr",
            private=True,
            exclude_globs=exclude or [],
            include_globs=include or [],
            creation_date=datetime.now(),
            comment="Created by Uploadrr",
            created_by="Created by Uploadrr"
        )

    def _calculate_piece_size(self, file_size: int, piece_size_max: Optional[int]) -> Tuple[int, str]:
        """Calculate optimal piece size."""
        size_thresholds = [
            (268435456, 18, "256KiB"),    # 256 MiB
            (1073741824, 19, "512KiB"),   # 1 GiB
            (2147483648, 20, "1MiB"),     # 2 GiB
            (4294967296, 21, "2MiB"),     # 4 GiB
            (8589934592, 22, "4MiB"),     # 8 GiB
            (17179869184, 23, "8MiB"),    # 16 GiB
        ]
        
        if piece_size_max:
            for threshold, size, text in size_thresholds:
                if piece_size_max >= (size - 17):
                    return size, text
        
        for threshold, size, text in size_thresholds:
            if file_size < threshold:
                return size, text
        return 24, "16MiB"

    async def _generates(self, torrent: Torrent, path: Path, output_path: Path, piece_size: int, method: Optional[str]):
        """Generate torrent file with consistent content rules."""
        exclude_globs = torrent.exclude_globs
        include_globs = torrent.include_globs
        threads = self.config['DEFAULT'].get('threads')
        if threads == 'max':
            threads = os.cpu_count()

        output_path = output_path.resolve()

        if method == 'torrenttools':
            if self._check_tool('torrenttools'):
                args = [
                    'torrenttools', 'create',
                    '-a', self.tracker,
                    '--private', 'on',
                    '--piece-size', str(2**piece_size),
                    '--created-by', "Created by Uploadrr",
                    '--no-cross-seed',
                    '-o', str(output_path),
                ]
                if threads:
                    args.extend(['--threads', str(threads)])
                for pattern in exclude_globs:
                    if pattern != '._*':
                        args.extend(['--exclude', pattern])    
                for pattern in include_globs:
                    args.extend(['--include', pattern])
                args.append(str(path))
                await self._hash_spinner(args, "torrenttools")
            else:
                console.print("[bold red]torrenttools not found in PATH, falling back to torf...")
                await self.use_torf(torrent, path, output_path, piece_size)

        elif method == 'mktorrent':
            if self._check_tool('mktorrent'):
                if output_path.exists():
                    try:
                        output_path.unlink()
                    except OSError as e:
                        log.error(f"[bold]Failed to remove {output_path} ({e}), falling back to torf...")
                        await self.use_torf(torrent, path, output_path, piece_size)
                        return

                if Path(path).is_dir() and exclude_globs:
                    try:
                        with tempfile.TemporaryDirectory() as temp_dir:
                            temp_path = Path(temp_dir)
                            original_name = Path(path).name
                            target_dir = temp_path / original_name
                            target_dir.mkdir()
                            self._filter_directory_with_symlinks(path, target_dir, exclude_globs, include_globs)

                            args = [
                                'mktorrent',
                                '-a', self.tracker,
                                '-p',
                                '-l', str(piece_size),
                                '-s', 'Uploadrr',
                                '-o', str(output_path),
                                target_dir
                            ]
                            await self._hash_spinner(args, "mktorrent")                        
                    except (OSError, subprocess.CalledProcessError) as e:
                        log.error(f"[bold]mktorrent failed ({e}), falling back to torf...")
                        await self.use_torf(torrent, path, output_path, piece_size)
                else:
                    args = [
                        'mktorrent',
                        '-a', self.tracker,
                        '-p',
                        '-l', str(piece_size),
                        '-s', 'Uploadrr',
                        '-o', str(output_path),
                        str(path)
                    ]
                    try:
                        await self._hash_spinner(args, "mktorrent")
                    except subprocess.CalledProcessError as e:
                        console.print(f"[bold red]mktorrent failed ({e}), falling back to torf...")
                        await self.use_torf(torrent, path, output_path, piece_size)
            else:
                console.print("[bold red]mktorrent not found in PATH, falling back to torf...")
                await self.use_torf(torrent, path, output_path, piece_size)

        else:  # Default to torf
            await self.use_torf(torrent, path, output_path, piece_size)

    async def _use_torf(self, torrent: Torrent, path: Path, output_path: Path, piece_size: int) -> None:
        """Fallback to torf with consistent settings."""
        torrent.piece_size = 2**piece_size
        torrent.piece_size_max = 16777216
        torrent.generate(callback=self._torf_callback, interval=5)
        torrent.write(output_path, overwrite=True)
        torrent.verify_filesize(path)


    def _filter_directory_with_symlinks(self, source_path: Path, dest_path: Path, exclude_globs: list, include_globs: list) -> None:
        """Filter directory contents using symlinks to match exclusion rules for mktorrent."""
        source_path = Path(source_path)
        dest_path = Path(dest_path)
        
        for item in source_path.rglob('*'):
            relative_path = item.relative_to(source_path)
            item_name = item.name
            
            if any(glob.fnmatch.fnmatch(item_name, pat) for pat in exclude_globs):
                continue
            if include_globs and not any(glob.fnmatch.fnmatch(item_name, pat) for pat in include_globs):
                continue
                
            dest_item = dest_path / relative_path
            dest_item.parent.mkdir(parents=True, exist_ok=True)
            if item.is_file():
                os.symlink(item, dest_item)

    async def _hash_spinner(self, args, tool_name):
        """Execute subprocess with a live spinner for feedback"""
        log.ddebug(f"Starting {tool_name} with args: {args}")
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            log.ddebug(f"{tool_name} process started with PID: {process.pid}")
            
            with Progress(
                SpinnerColumn(),
                "[progress.description]{task.description}"
            ) as progress:
                task = progress.add_task(f"Hashing with {tool_name}...", total=None)
                start_time = time.time()
                stdout, stderr = await process.communicate()               
                end_time = time.time()
                elapsed_time = end_time - start_time
                log.ddebug(f"{tool_name} stdout: {stdout.decode().strip()}")
                log.ddebug(f"{tool_name} stderr: {stderr.decode().strip()}")
                log.debug(f"Hashed in {elapsed_time:.2f} seconds")

                if process.returncode != 0:
                    safe_args = args.copy()
                    safe_args[2] = "OMITTED"
                    log.error(f"[bold red]{tool_name} failed with code {process.returncode}: {stderr.decode()}")
                    raise subprocess.CalledProcessError(process.returncode, args, stderr=stderr)

        except FileNotFoundError:
            log.error(f"[bold red]{tool_name} not found in system PATH")
            raise
        except asyncio.CancelledError:
            log.debug(f"{tool_name} cancelled by SIGINT")
            if process.returncode is None:
                process.terminate()
                await process.wait()
            raise
        finally:
            if 'process' in locals() and process.returncode is None:
                process.terminate()
                await process.wait()
                log.debug(f"{tool_name} process forcefully terminated")

    def _check_tool(self, tool_name: str) -> bool:
        return shutil.which(tool_name) is not None

    def _torf_callback(self, torrent: Torrent, filepath: str, pieces_done: int, pieces_total: int) -> None:
        """Progress callback for torf torrent generation."""
        if not hasattr(self, 'progress'):
            self.progress = Progress(
                "[progress.description]{task.description}",
                BarColumn(),
                "[progress.percentage]{task.percentage:>3.1f}%",
                TimeRemainingColumn()
            )
            self.task = self.progress.add_task("Hashing...", total=pieces_total)
            self.progress.start()
            self.start_time = time.time()        

        self.progress.update(self.task, completed=pieces_done)
        if pieces_done >= pieces_total:
            end_time = time.time()
            elapsed_time = end_time - self.start_time 
            log.debug(f" Hashed in {elapsed_time:.2f} seconds")
            self.progress.stop()
            del self.progress
            del self.start_time

    async def create_random_torrents(self, base_dir: Path, uuid: str, num: int, path: Path) -> None:
        """Create multiple randomized torrent copies."""
        manual_name = re.sub(r"[^0-9a-zA-Z\[\]\'\-]+", ".", os.path.basename(path))
        base_path = Path(base_dir) / "tmp" / uuid / "BASE.torrent"
        base_torrent = Torrent.read(str(base_path))
        
        tasks = []
        for i in range(1, int(num) + 1):
            new_torrent = base_torrent.copy()
            new_torrent.metainfo['info']['entropy'] = random.randint(1, 999999)
            output_path = Path(base_dir) / "tmp" / uuid / f"[RAND-{i}]{manual_name}.torrent"
            tasks.append(asyncio.to_thread(new_torrent.write, str(output_path), overwrite=True))
        
        await asyncio.gather(*tasks)

    async def from_existing(self, torrentpath: Path, base_dir: Path, uuid: str) -> None:
        """Create base torrent from existing torrent with cleaned metadata."""
        if not Path(torrentpath).exists():
            return
        
        base_torrent = Torrent.read(torrentpath)
        base_torrent.creation_date = datetime.now()
        base_torrent.trackers = [self.tracker]
        base_torrent.comment = "Created by Uploadrr"
        base_torrent.created_by = "Created by Uploadrr"
        base_torrent.source = 'Uploadrr'
        base_torrent.private = True
        
        valid_info_keys = {'files', 'length', 'name', 'piece length', 'pieces', 'private', 'source'}
        valid_meta_keys = {'announce', 'comment', 'creation date', 'created by', 'encoding', 'info'}
        
        base_torrent.metainfo['info'] = {
            k: v for k, v in base_torrent.metainfo['info'].items()
            if k in valid_info_keys
        }
        
        for key in list(base_torrent.metainfo.keys()):
            if key not in valid_meta_keys:
                del base_torrent.metainfo[key]
        
        output_path = base_dir / "tmp" / uuid / "BASE.torrent"
        await asyncio.to_thread(base_torrent.write, str(output_path), overwrite=True)