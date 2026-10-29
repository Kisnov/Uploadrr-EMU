from __future__ import annotations

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
from typing import Dict, Optional, Tuple, Union

import bencodepy as bencode
from torf import TorfError, Torrent

from src.cleanrr import running_subprocesses
from src.progress import ProgressManager
from src.utils.console import log


class TorrentMaker:
    def __init__(self, config):
        self.config = config
        self.tracker = "https://fake.tracker"

    async def create(self, meta: dict, path: Path, piece_size_max: Optional[int] = None) -> Torrent:
        """Create a new torrent file with consistent content rules across tools."""
        progress = ProgressManager.get_instance()
        if not progress:
            log.error("ProgressManager not initialized")
            raise RuntimeError("ProgressManager not initialized")

        try:
            piece_size_max = int(piece_size_max) if piece_size_max is not None else 0
            path = self._prepare_path(meta, path)
            exclude, include = self._get_filters(meta)

            torrent = self._initialize(path, exclude, include)
            piece_size, piece_size_text = self._calculate_piece_size(torrent.size, piece_size_max)

            progress.update(f"Creating .torrent with piece size {piece_size_text}...")

            torrent_creation = meta.get("torrent_creation")
            log.debug(f"Tool: {torrent_creation}")
            output_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "BASE.torrent"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            log.debug(f"Path used for torrent: {path}")
            log.debug(f"Exclude patterns: {exclude}")
            log.debug(f"Include patterns: {include}")
            log.debug(f"Output path: {output_path}")
            log.debug(f"Using method: {torrent_creation}")

            await self._generate(torrent, path, output_path, piece_size, torrent_creation)

            progress.update(".torrent created")
            return torrent
        except ValueError as e:
            log.error(f"Invalid input: {e}")
            raise
        except Exception as e:
            log.error(f"Torrent creation failed: {e}")
            raise

    def _prepare_path(self, meta: dict, path: Path) -> str:
        """Prepare the path based on metadata conditions."""
        if not meta.get("full_dir") and meta.get("is_dir"):
            os.chdir(path)
            valid_extensions = {".mkv", ".mp4", ".ts"}
            files = [f for f in Path(path).iterdir() if f.suffix.lower() in valid_extensions and "sample" not in f.name.lower()]
            if len(files) == 1:
                return meta["filelist"][0]
        return str(path)

    def _get_filters(self, meta: dict) -> Tuple[list, list]:
        """Determine include/exclude filters based on metadata."""
        if any(meta.get(k) for k in ["full_dir", "is_disc", "is_music"]):
            desc = meta["uuid"] if meta.get("is_dir", False) else Path(meta["uuid"]).stem
            return ["._*", "description.txt", f"{desc}.txt"], []
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
            created_by="Created by Uploadrr",
        )

    def _calculate_piece_size(self, file_size: int, piece_size_max: Optional[int]) -> Tuple[int, str]:
        """Calculate optimal piece size."""
        size_thresholds = [
            (268435456, 18, "256KiB"),  # 256 MiB
            (1073741824, 19, "512KiB"),  # 1 GiB
            (2147483648, 20, "1MiB"),  # 2 GiB
            (4294967296, 21, "2MiB"),  # 4 GiB
            (8589934592, 22, "4MiB"),  # 8 GiB
            (17179869184, 23, "8MiB"),  # 16 GiB
        ]

        if piece_size_max:
            for threshold, size, text in size_thresholds:
                if piece_size_max >= (size - 17):
                    return size, text

        for threshold, size, text in size_thresholds:
            if file_size < threshold:
                return size, text
        return 24, "16MiB"

    async def _generate(self, torrent: Torrent, path: Path, output_path: Path, piece_size: int, method: Optional[str]):
        """Generate torrent file with consistent content rules."""
        exclude_globs = torrent.exclude_globs
        include_globs = torrent.include_globs
        threads = self.config["DEFAULT"].get("threads")
        if threads == "max":
            threads = os.cpu_count()

        output_path = output_path.resolve()
        progress = ProgressManager.get_instance()

        if method == "torrenttools":
            if self._check_tool("torrenttools"):
                args = [
                    "torrenttools",
                    "create",
                    "-a",
                    self.tracker,
                    "--private",
                    "on",
                    "--piece-size",
                    str(2**piece_size),
                    "--created-by",
                    "Created by Uploadrr",
                    "--no-cross-seed",
                    "-o",
                    str(output_path),
                ]
                if threads:
                    args.extend(["--threads", str(threads)])
                for pattern in exclude_globs:
                    if pattern != "._*":
                        args.extend(["--exclude", pattern])
                for pattern in include_globs:
                    args.extend(["--include", pattern])
                args.append(str(path))
                await self._hash_spinner(args, "torrenttools")

                # Validate output torrent
                if not output_path.exists() or output_path.stat().st_size == 0:
                    log.warning(f"Empty or missing torrent file at {output_path}. Falling back to torf...")
                    await self.use_torf(torrent, path, output_path, piece_size)
                    return
            else:
                progress.update("torrenttools not found in PATH, falling back to torf...")
                await self.use_torf(torrent, path, output_path, piece_size)

        elif method == "mktorrent":
            if self._check_tool("mktorrent"):
                if output_path.exists():
                    try:
                        output_path.unlink()
                    except OSError as e:
                        log.error(f"Failed to remove {output_path} ({e}), falling back to torf...")
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

                            # Validate temporary directory contents
                            temp_files = list(target_dir.rglob("*"))
                            if not temp_files:
                                log.warning(f"No files in temporary directory {target_dir} after filtering. Falling back to torf...")
                                await self.use_torf(torrent, path, output_path, piece_size)
                                return

                            args = [
                                "mktorrent",
                                "-a",
                                self.tracker,
                                "-p",
                                "-l",
                                str(piece_size),
                                "-s",
                                "Uploadrr",
                                "-o",
                                str(output_path),
                                target_dir,
                            ]
                            await self._hash_spinner(args, "mktorrent")

                            # Validate output torrent
                            if not output_path.exists() or output_path.stat().st_size == 0:
                                log.warning(f"Empty or missing torrent file at {output_path}. Falling back to torf...")
                                await self.use_torf(torrent, path, output_path, piece_size)
                                return

                    except (OSError, subprocess.CalledProcessError) as e:
                        log.error(f"mktorrent failed ({e}), falling back to torf...")
                        await self.use_torf(torrent, path, output_path, piece_size)
                else:
                    args = [
                        "mktorrent",
                        "-a",
                        self.tracker,
                        "-p",
                        "-l",
                        str(piece_size),
                        "-s",
                        "Uploadrr",
                        "-o",
                        str(output_path),
                        str(path),
                    ]
                    try:
                        await self._hash_spinner(args, "mktorrent")

                        # Validate output torrent
                        if not output_path.exists() or output_path.stat().st_size == 0:
                            log.warning(f"Empty or missing torrent file at {output_path}. Falling back to torf...")
                            await self.use_torf(torrent, path, output_path, piece_size)
                            return

                    except subprocess.CalledProcessError as e:
                        log.error(f"mktorrent failed ({e}), falling back to torf...")
                        await self.use_torf(torrent, path, output_path, piece_size)
            else:
                progress.update("mktorrent not found in PATH, falling back to torf...")
                await self.use_torf(torrent, path, output_path, piece_size)

        elif method == "mkbrr":
            if self._check_tool("mkbrr"):
                await self._use_mkbrr(torrent, path, output_path, piece_size, threads)
            else:
                progress.update("mkbrr not found in PATH, falling back to torf...")
                await self.use_torf(torrent, path, output_path, piece_size)

        else:  # Default to torf
            await self.use_torf(torrent, path, output_path, piece_size)

    async def _use_mkbrr(self, torrent: Torrent, path: Path, output_path: Path, piece_size: int, threads) -> None:
        """mkbrr sobre los mismos ficheros que elegiría torf; si su .torrent no lista esos ficheros, torf."""
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(path)
            if target.is_dir():
                # Los filtros los aplica torf: mkbrr hashea un árbol de enlaces con solo los ficheros elegidos.
                target = Path(temp_dir) / target.name
                for filepath in torrent.filepaths:
                    link = target / Path(filepath).relative_to(path)
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.symlink_to(filepath)
            args = [
                "mkbrr",
                "create",
                str(target),
                "-t",
                self.tracker,
                "-p",
                "-l",
                str(piece_size),
                "-s",
                "Uploadrr",
                "-c",
                "Created by Uploadrr",
                "-o",
                str(output_path),
                "--skip-prefix",
            ]
            if threads:
                args.extend(["--workers", str(threads)])
            output_path.unlink(missing_ok=True)
            returncode = await self._run_mkbrr(args)
        if returncode == 0 and self._same_files(output_path, torrent):
            ProgressManager.get_instance().update("mkbrr hashing complete")
            return
        log.warning("mkbrr failed or listed other files, falling back to torf...")
        await self.use_torf(torrent, path, output_path, piece_size)

    async def _run_mkbrr(self, args: list) -> int:
        """Ejecuta mkbrr pasando su porcentaje a la barra de progreso. Devuelve el código de salida."""
        progress = ProgressManager.get_instance()
        progress.update("Hashing with mkbrr: 0.0%")
        process = await asyncio.create_subprocess_exec(*args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        running_subprocesses.add(process)
        last = None
        try:
            while chunk := await process.stdout.read(4096):
                found = re.findall(rb"(\d{1,3})%", chunk)
                if found and found[-1] != last:
                    last = found[-1]
                    progress.update(f"Hashing with mkbrr: {int(last):.1f}%")
            return await process.wait()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            running_subprocesses.discard(process)

    @staticmethod
    def _same_files(output_path: Path, torrent: Torrent) -> bool:
        """Si el .torrent escrito tiene el nombre y los ficheros (ruta y tamaño) que eligió torf."""
        try:
            made = Torrent.read(output_path, validate=False)
        except (TorfError, OSError):
            return False
        listed = sorted((str(f), f.size) for f in made.files)
        return made.name == torrent.name and listed == sorted((str(f), f.size) for f in torrent.files)

    async def use_torf(self, torrent: Torrent, path: Path, output_path: Path, piece_size: int) -> None:
        """Fallback to torf with consistent settings and ProgressManager feedback."""
        progress = ProgressManager.get_instance()
        if not progress:
            log.error("ProgressManager not initialized")
            return

        torrent.piece_size = 2**piece_size
        torrent.piece_size_max = 16777216
        progress.update("Starting torf hashing...")
        torrent.generate(callback=self._torf_callback, interval=5)
        torrent.write(output_path, overwrite=True)
        torrent.verify_filesize(path)
        progress.update("Torf hashing complete")

    def _filter_directory_with_symlinks(self, source_path: Path, dest_path: Path, exclude_globs: list, include_globs: list) -> None:
        """Filter directory contents using symlinks to match exclusion rules for mktorrent, preserving directory structure."""
        source_path = Path(source_path)
        dest_path = Path(dest_path)

        # Collect all files, including hidden ones
        all_files = list(source_path.rglob("*")) + list(source_path.rglob(".*"))
        log.debug(f"Found {len(all_files)} items in {source_path}: {[str(f) for f in all_files]}")

        included_files = []
        for item in all_files:
            if not item.is_file():
                log.debug(f"Skipping {item.name} (not a file, likely a directory)")
                continue

            relative_path = item.relative_to(source_path)
            item_name = item.name

            # Check exclusion
            matching_exclude_patterns = [pat for pat in exclude_globs if glob.fnmatch.fnmatch(item_name, pat)]
            if matching_exclude_patterns:
                log.debug(f"Excluding {relative_path} (matches exclude pattern(s): {', '.join(matching_exclude_patterns)})")
                continue

            if include_globs and not any(glob.fnmatch.fnmatch(item_name, pat) for pat in include_globs):
                log.debug(f"Excluding {relative_path} (does not match include pattern)")
                continue

            dest_item = dest_path / relative_path
            dest_item.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(item, dest_item)
                included_files.append(str(relative_path))
            except OSError as e:
                log.warning(f"Failed to create symlink for {relative_path}: {e}")
                continue

        log.debug(f"Included {len(included_files)} files in temporary directory {dest_path}: {included_files}")
        if not included_files:
            log.warning(
                f"No files included in temporary directory {dest_path}. Check exclude/include patterns or source directory contents."
            )

    async def _hash_spinner(self, args, tool_name):
        """Execute subprocess with feedback via ProgressManager."""
        progress = ProgressManager.get_instance()
        if not progress:
            log.error("ProgressManager not initialized")
            return
        log.ddebug(f"Starting {tool_name} with args: {args}")
        process = None
        try:
            progress.update(f"Hashing with {tool_name}...")
            process = await asyncio.create_subprocess_exec(*args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            running_subprocesses.add(process)  # Add to global set for cleanup
            log.ddebug(f"{tool_name} process started with PID: {process.pid}")
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
            if process and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            raise
        except Exception as e:
            log.error(f"Unexpected error in {tool_name}: {e}")
            raise
        finally:
            if process:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                    log.debug(f"{tool_name} process forcefully terminated")
                running_subprocesses.discard(process)
            progress.update("Hashing complete")

    def _check_tool(self, tool_name: str) -> bool:
        return shutil.which(tool_name) is not None

    def _torf_callback(self, torrent: Torrent, filepath: str, pieces_done: int, pieces_total: int) -> None:
        """Progress callback for torf torrent generation using ProgressManager."""
        progress = ProgressManager.get_instance()
        if not progress:
            return

        percentage = (pieces_done / pieces_total) * 100 if pieces_total > 0 else 0
        elapsed_time = time.time() - getattr(self, "start_time", time.time())
        self.start_time = time.time()  # Initialize start_time if not set

        progress.update(f"Hashing with torf: {percentage:.1f}% ({pieces_done}/{pieces_total} pieces)")

        if pieces_done >= pieces_total:
            log.debug(f"Hashed in {elapsed_time:.2f} seconds")

    async def create_random_torrents(self, base_dir: Path, uuid: str, num: int, path: Path) -> None:
        """Create multiple randomized torrent copies."""
        manual_name = re.sub(r"[^0-9a-zA-Z\[\]\'\-]+", ".", Path(path).name)
        base_path = Path(base_dir) / "tmp" / uuid / "BASE.torrent"
        base_torrent = Torrent.read(str(base_path))

        tasks = []
        for i in range(1, int(num) + 1):
            new_torrent = base_torrent.copy()
            new_torrent.metainfo["info"]["entropy"] = random.randint(1, 999999)
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
        base_torrent.source = "Uploadrr"
        base_torrent.private = True

        valid_info_keys = {"files", "length", "name", "piece length", "pieces", "private", "source"}
        valid_meta_keys = {"announce", "comment", "creation date", "created by", "encoding", "info"}

        base_torrent.metainfo["info"] = {k: v for k, v in base_torrent.metainfo["info"].items() if k in valid_info_keys}

        for key in list(base_torrent.metainfo.keys()):
            if key not in valid_meta_keys:
                del base_torrent.metainfo[key]

        output_path = base_dir / "tmp" / uuid / "BASE.torrent"
        await asyncio.to_thread(base_torrent.write, str(output_path), overwrite=True)


# Avoid loading in TorrentMaker Intsance
async def edit_torrent(
    meta: Optional[dict] = None,
    torrent_path: Optional[Union[str, Path]] = None,
    tracker: Optional[str] = None,
    source_flag: Optional[str] = None,
    announce_url: Optional[str] = None,
    comment: Optional[str] = None,
) -> Path:
    """Edit an existing torrent by updating tracker, source flag, and/or comment."""
    try:
        if torrent_path is not None:
            existing = Path(torrent_path)
            if not existing.exists():
                raise FileNotFoundError(f"Torrent not found at {existing}")
        elif meta is not None:
            torrent_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
            existing = torrent_path / "BASE.torrent"
            if not existing.exists():
                log.error(f"Base torrent not found at {existing}")
                raise FileNotFoundError(f"Base torrent not found at {existing}")
        else:
            raise ValueError("Either 'meta' or 'torrent_path' must be provided")

        if meta is not None and tracker is not None:
            output_dir = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
            output_path = output_dir / f"[{tracker}]{meta['clean_name']}.torrent"
        elif torrent_path is not None:
            input_path = Path(torrent_path)
            output_path = input_path
        else:
            raise ValueError("Cannot determine output path: need meta+tracker or torrent_path")

        new_torrent = Torrent.read(existing)

        if meta is not None:
            valid_meta_keys = {"creation date", "encoding", "info"}
            for key in list(new_torrent.metainfo.keys()):
                if key not in valid_meta_keys:
                    new_torrent.metainfo.pop(key, None)
            new_torrent.metainfo["created by"] = "Uploadrr"

        if announce_url is not None:
            new_torrent.metainfo["announce"] = announce_url.strip()

        if source_flag is not None:
            new_torrent.metainfo["info"]["source"] = source_flag

        if comment is not None:
            if comment == "":
                new_torrent.metainfo.pop("comment", None)
            else:
                new_torrent.metainfo["comment"] = comment

        Torrent.copy(new_torrent).write(output_path, overwrite=True)
        return output_path

    except Exception as e:
        log.error(f"Failed to edit torrent: {e}")
        raise


async def handle_torrent_creation(config: Dict, meta: Dict) -> Dict:
    """Handle torrent file creation or reuse."""
    maketorrent = TorrentMaker(config)
    torrent_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / "BASE.torrent"
    rehash = meta.get("rehash", False)
    nohash = meta.get("nohash", False)
    randomized = int(meta.get("randomized", 0))
    piece_size_max = meta.get("piece_size_max", 0)
    path = Path(meta["path"])

    if nohash:
        meta["client"] = "none"
    else:
        if rehash or not torrent_path.exists():
            if not rehash:
                from src.clients import Clients

                client = Clients(config)
                reuse_torrent = await client.find_existing_torrent(meta)
                if reuse_torrent:
                    await maketorrent.from_existing(reuse_torrent, Path(meta["base_dir"]), meta["uuid"])
                    return meta
            await maketorrent.create(meta, path, piece_size_max)

    if randomized >= 1:
        await maketorrent.create_random_torrents(Path(meta["base_dir"]), meta["uuid"], randomized, path)
    return meta


def extract_size_from_torrent(base_dir: str, uuid: str) -> int:
    """Extract size from torrent file if it exists, return 0 if not."""
    base_torrent = Path(base_dir) / "tmp" / f"{uuid}" / "BASE.torrent"
    try:
        if base_torrent.exists():
            with open(base_torrent, "rb") as f:
                torrent_data = bencode.decode(f.read())
            info = torrent_data[b"info"]
            return sum(file[b"length"] for file in info[b"files"]) if b"files" in info else info[b"length"]
        return 0
    except (FileNotFoundError, KeyError, bencode.BencodeDecodeError) as e:
        log.debug(f"Could not extract size from torrent: {e}")
        return 0
