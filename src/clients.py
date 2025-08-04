# -*- coding: utf-8 -*-
import asyncio
import base64
import errno
import json
import shutil
import ssl
import time
import xmlrpc.client
from pathlib import Path
from typing import Dict, Optional, Tuple

import bencode
import httpx
import qbittorrentapi
from deluge_client import DelugeRPCClient
from pyrobase.parts import Bunch
from torf import Torrent

from src.utils.console import console, log


class Clients:
    """Handles adding torrents to various torrent clients with an optimized index."""

    def __init__(self, config):
        self.config = config
        self.index_dir = Path(__file__).resolve().parent.parent / "data"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.index_cache: Dict[str, Dict] = {}  # In-memory cache for indexes

    async def find_existing_torrent(self, meta: dict) -> Optional[Path]:
        """Search for an existing torrent in the client's storage directory or index."""
        torrent_client = self._get_torrent_client(meta)
        if not torrent_client or torrent_client == "none":
            log.debug("No torrent client specified.")
            return None

        client = self.config["TORRENT_CLIENTS"][torrent_client]
        client_type = client["torrent_client"].lower()
        torrent_storage_dir = Path(client.get("torrent_storage_dir", ""))

        log.debug(f"Searching for existing torrent for {meta.get('clean_name', 'unknown')} in {torrent_storage_dir}")

        if not torrent_storage_dir.exists() and client_type != "watch":
            console.print(f"[bold red]Invalid torrent_storage_dir path: {torrent_storage_dir}")
            return None

        torrenthash = await self._get_valid_torrenthash(meta, torrent_storage_dir, client_type, client)
        if not torrenthash:
            log.debug("No valid torrent hash found")
            return None

        torrent_path = torrent_storage_dir / f"{torrenthash}.torrent"
        valid, torrent_path = await self.is_valid_torrent(meta, torrent_path, torrenthash, client_type)
        log.debug(f"Torrent validation result: valid={valid}, path={torrent_path}")
        return torrent_path if valid else None

    async def is_valid_torrent(
        self, meta: dict, torrent_path: Path, torrenthash: str, torrent_client: str, print_err: bool = False
    ) -> Tuple[bool, Path]:
        """Validate a torrent file against metadata."""
        log.debug(f"Validating torrent {torrenthash} at {torrent_path}")

        torrenthash = torrenthash.lower().strip() if torrent_client in ("qbit", "deluge") else torrenthash.upper().strip()
        torrent_path = torrent_path.with_name(f"{torrenthash}.torrent")

        if meta.get("debug"):
            console.log(str(torrent_path))

        if not torrent_path.exists():
            console.print(f"[bold yellow]{torrent_path} not found")
            return False, torrent_path

        try:
            torrent = Torrent.read(torrent_path)
        except Exception as e:
            log.debug(f"Failed to read torrent file: {e}")
            return False, torrent_path

        valid, wrong_file = self._check_torrent_validity(torrent, meta)
        log.debug(f"Torrent validity check: valid={valid}, wrong_file={wrong_file}")

        if valid:
            valid, err_print = self._validate_torrent_properties(torrent, torrenthash, wrong_file)
            log.debug(f"Torrent properties validation: valid={valid}, message={err_print}")
        else:
            err_print = "[bold yellow]Unwanted Files/Folders Identified"

        if print_err:
            console.print(err_print)
        return valid, torrent_path

    def _normalize_torrent_path(self, torrent: dict, local_path: str, remote_path: str) -> Path:
        """Normalize torrent path using client remote mapping."""
        raw_path = torrent.get("content_path", f"{torrent.get('save_path', '')}{torrent.get('name', '')}")
        normalized = Path(raw_path)
        if local_path.lower() in normalized.as_posix().lower() and local_path != remote_path:
            normalized = Path(str(normalized).replace(remote_path, local_path))
        return normalized

    async def _search_torrents(
        self, torrents: list, meta: dict, torrent_storage_dir: Path, local_path: str, remote_path: str, exact: bool, client_type: str
    ) -> Optional[str]:
        """Search torrents using the index for efficiency."""
        meta_path = Path(meta["path"])
        meta_dir = meta_path.parent
        meta_file = meta_path.name if len(meta.get("filelist", [])) == 1 else None
        index_key = f"{client_type}_index.json"
        index_path = self.index_dir / index_key

        # Load or update index
        if index_key not in self.index_cache:
            await self._update_index(torrents, index_path, client_type, local_path, remote_path)

        index = self.index_cache.get(index_key, {})

        # Search index for matches
        for torrent_hash, torrent_data in index.items():
            torrent_path = Path(torrent_data["path"])
            if self._is_torrent_match(torrent_path, meta_path, meta_dir, meta_file, exact):
                valid, _ = await self.is_valid_torrent(
                    meta, torrent_storage_dir / f"{torrent_hash}.torrent", torrent_hash, client_type
                )
                if valid:
                    console.print(f"[green]Found a matching .torrent with hash: [bold yellow]{torrent_hash}")
                    return torrent_hash
        return None

    async def _update_index(self, torrents: list, index_path: Path, client_type: str, local_path: str, remote_path: str) -> None:
        """Update the torrent index file and cache."""
        index = {}
        for torrent in torrents:
            torrent_hash = torrent.get("hash", torrent.get("hashString"))
            if not torrent_hash:
                continue
            torrent_path = self._normalize_torrent_path(torrent, local_path, remote_path).as_posix()
            index[torrent_hash] = {"path": torrent_path, "name": torrent.get("name")}

        self.index_cache[f"{client_type}_index.json"] = index
        try:
            with index_path.open("w") as f:
                json.dump(index, f, indent=2)
        except Exception as e:
            log.debug(f"Failed to write index file {index_path}: {e}")

    async def search_qbit_for_torrent(self, meta: dict, client: dict) -> Optional[str]:
        """Search qBittorrent for an existing torrent using the index."""
        console.print("[green]Searching qBittorrent for an existing .torrent")
        torrent_storage_dir = Path(client.get("torrent_storage_dir", ""))
        local_path, remote_path = await self.remote_path_map(meta)

        qbt_client = qbittorrentapi.Client(
            host=client["qbit_url"],
            port=client["qbit_port"],
            username=client["qbit_user"],
            password=client["qbit_pass"],
            VERIFY_WEBUI_CERTIFICATE=client.get("VERIFY_WEBUI_CERTIFICATE", True),
        )
        try:
            qbt_client.auth_log_in()
        except qbittorrentapi.LoginFailed:
            console.print("[bold red]INCORRECT QBIT LOGIN CREDENTIALS")
            return None

        torrents = qbt_client.torrents_info()

        # Try exact match first
        match = await self._search_torrents(
            torrents, meta, torrent_storage_dir, local_path, remote_path, exact=True, client_type="qbit"
        )
        if match:
            return match

        # Fallback to directory or filename match
        match = await self._search_torrents(
            torrents, meta, torrent_storage_dir, local_path, remote_path, exact=False, client_type="qbit"
        )
        if match:
            return match

        log.debug("No matching torrent found in qBittorrent")
        return None

    async def search_transmission_for_torrent(self, meta: dict, client: dict) -> Optional[str]:
        """Search Transmission for an existing torrent using the index."""
        console.print("[green]Searching Transmission for an existing .torrent")
        torrent_storage_dir = Path(client.get("torrent_storage_dir", ""))

        meta_path = Path(meta["path"])
        meta_dir = meta_path.parent
        meta_file = meta_path.name if len(meta.get("filelist", [])) == 1 else None

        async with httpx.AsyncClient(auth=(client.get("transmission_user"), client.get("transmission_pass"))) as http_client:
            torrents_info = await self.transmission_rpc_call(
                http_client, "torrent-get", {"fields": ["id", "name", "hashString", "downloadDir"]}
            )
            if not torrents_info or "torrents" not in torrents_info["arguments"]:
                log.debug("No torrents found in Transmission")
                return None

            local_path, remote_path = await self.remote_path_map(meta)
            # Exact match
            match = await self._search_torrents(
                torrents_info["arguments"]["torrents"],
                meta,
                torrent_storage_dir,
                local_path,
                remote_path,
                exact=True,
                client_type="transmission",
            )
            if match:
                return match

            # Fallback match
            match = await self._search_torrents(
                torrents_info["arguments"]["torrents"],
                meta,
                torrent_storage_dir,
                local_path,
                remote_path,
                exact=False,
                client_type="transmission",
            )
            if match:
                return match

        log.debug("No matching torrent found in Transmission")
        return None

    def _check_torrent_validity(self, torrent: Torrent, meta: dict) -> Tuple[bool, bool]:
        """Check if torrent files match meta data with improved validation."""
        if meta.get("full_dir", False):
            meta_path = Path(meta["path"])
            meta_files = [p.name for p in meta_path.rglob("*") if p.is_file() and not p.name.startswith("._")]
        else:
            if not meta.get("filelist"):
                log.debug("No filelist in meta, cannot validate torrent")
                return False, False
            meta_files = [Path(f).name for f in meta["filelist"]]

        torrent_files = [Path(f).name for f in torrent.files]

        log.debug(f"Torrent files: {torrent_files}")
        log.debug(f"Meta files: {meta_files}")

        # Check for unexpected parent folder
        torrent_base_dirs = {Path(f).parts[0] for f in torrent.files if len(Path(f).parts) > 1}
        if len(torrent_base_dirs) == 1:
            parent_dir = next(iter(torrent_base_dirs))
            expected_dir = Path(meta["path"]).name
            if parent_dir.lower() != expected_dir.lower():
                log.debug(f"Torrent folder '{parent_dir}' does not match expected directory '{expected_dir}'")
                return False, False

        # Handle disc torrents
        if meta.get("is_disc"):
            meta_path = Path(meta["path"])
            torrent_common_path = Path(torrent.files[0]).parent if torrent.files else None
            if torrent_common_path and meta_path.name.lower() in torrent_common_path.as_posix().lower():
                return True, False

        if len(torrent_files) == len(meta_files):
            if sorted(torrent_files) == sorted(meta_files):
                return True, False
            return False, True

        return False, False

    def _is_torrent_match(self, torrent_path: Path, meta_path: Path, meta_dir: Path, meta_file: Optional[str], exact: bool) -> bool:
        """Check if a torrent path matches the meta path or directory/file."""
        torrent_dir = torrent_path.parent
        torrent_file = torrent_path.name

        if exact:
            if torrent_path.as_posix().lower() == meta_path.as_posix().lower():
                log.debug("Exact path match found")
                return True
            if meta_file and (torrent_path.as_posix().lower() == Path(meta_dir / meta_file).as_posix().lower()):
                log.debug("Exact file path match found")
                return True
        else:
            if torrent_dir.as_posix().lower() == meta_dir.as_posix().lower():
                log.debug("Directory match found")
                return True
            if meta_file and torrent_file.lower() == meta_file.lower():
                log.debug("File name match found")
                return True

        return False

    async def add_to_client(self, meta: dict, tracker: str) -> None:
        """Add a torrent to the specified client."""
        torrent_path = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{tracker}]{meta['clean_name']}.torrent"
        log.debug(f"Attempting to add torrent at {torrent_path} to client")

        if meta.get("no_seed", False):
            console.print("[bold red]--no_seed passed; torrent not added to client")
            console.print("[bold yellow]Add torrent manually to the client")
            return

        if not torrent_path.exists():
            log.debug(f"Torrent file not found at {torrent_path}")
            return

        torrent = Torrent.read(torrent_path)
        torrent_client = self._get_torrent_client(meta)
        if not torrent_client or torrent_client == "none":
            log.debug("No valid torrent client specified; skipping addition")
            return

        client = self.config["TORRENT_CLIENTS"][torrent_client]
        client_type = client["torrent_client"].lower()
        local_path, remote_path = await self.remote_path_map(meta)

        log.debug(f"Adding torrent to {client_type}")
        console.print(f"[bold green]Adding to {client_type}")

        path = Path(meta["path"])

        client_methods = {
            "rtorrent": self._add_to_rtorrent,
            "qbit": self._add_to_qbittorrent,
            "deluge": self._add_to_deluge,
            "transmission": self._add_to_transmission,
            "watch": self._add_to_watch_folder,
        }

        client_method = client_methods.get(client_type)
        if client_method:
            await client_method(path, torrent_path, torrent, meta, local_path, remote_path, client, tracker)
        else:
            console.print(f"[bold red]Unsupported torrent client: {client_type}")

    async def remote_path_map(self, meta: dict) -> Tuple[str, str]:
        """Map local paths to remote paths based on client configuration."""
        torrent_client = self._get_torrent_client(meta)
        client = self.config["TORRENT_CLIENTS"][torrent_client]

        local_path = client.get("local_path", "/LocalPath")
        remote_path = client.get("remote_path", "/RemotePath")

        if isinstance(local_path, list):
            for i, loc in enumerate(local_path):
                if Path(loc).as_posix().lower() in meta["path"].lower():
                    local_path = loc
                    remote_path = remote_path[i]
                    break
            else:
                local_path, remote_path = local_path[0], remote_path[0]

        local_path = Path(local_path).as_posix()
        remote_path = Path(remote_path).as_posix()
        if local_path.endswith("/"):
            remote_path += "/"

        return local_path, remote_path

    async def _add_to_rtorrent(
        self,
        save_path: Path,
        torrent_path: Path,
        torrent: Torrent,
        meta: dict,
        local_path: str,
        remote_path: str,
        client: dict,
        tracker,
    ) -> None:
        """Add torrent to rTorrent with fast resume support."""
        rtorrent = xmlrpc.client.Server(client["rtorrent_url"], context=ssl._create_stdlib_context())
        metainfo = bencode.bread(torrent_path)

        try:
            fast_resume = self.add_fast_resume(metainfo, str(save_path), torrent)
        except EnvironmentError as exc:
            console.print(f"[red]Error making fast-resume data ({exc})")
            raise

        if bencode.bencode(fast_resume) != metainfo:
            fr_file = torrent_path.with_suffix("-resume.torrent")
            console.print("Creating fast resume")
            bencode.bwrite(fast_resume, fr_file)
        else:
            fr_file = torrent_path

        path_str = self._adjust_path(save_path, local_path, remote_path)
        if not save_path.is_dir():
            path_str = str(save_path.parent)

        console.print("[bold yellow]Adding and starting torrent")
        rtorrent.load.start_verbose("", str(fr_file), f"d.directory_base.set={path_str}")
        time.sleep(1)

        for label in (client.get("rtorrent_label"), meta.get("rtorrent_label")):
            if label:
                rtorrent.d.custom1.set(torrent.infohash, label)

        if meta.get("debug"):
            console.print(f"[cyan]Path: {path_str}")

    async def _add_to_qbittorrent(
        self,
        save_path: Path,
        torrent_path: Path,
        torrent: Torrent,
        meta: dict,
        local_path: str,
        remote_path: str,
        client: dict,
        tracker: str,
    ) -> None:
        """Add torrent to qBittorrent with corrected tag application."""
        path_str = self._adjust_path(save_path, local_path, remote_path)
        if len(meta["filelist"]) == 1 and not save_path.is_dir():
            path_str = str(save_path.parent)
        if not path_str.endswith("/"):
            path_str += "/"

        qbt_client = qbittorrentapi.Client(
            host=client["qbit_url"],
            port=client["qbit_port"],
            username=client["qbit_user"],
            password=client["qbit_pass"],
            VERIFY_WEBUI_CERTIFICATE=client.get("VERIFY_WEBUI_CERTIFICATE", True),
        )

        console.print("[bold yellow]Adding and rechecking torrent")
        try:
            qbt_client.auth_log_in()
        except qbittorrentapi.LoginFailed:
            console.print("[bold red]INCORRECT QBIT LOGIN CREDENTIALS")
            return

        auto_management = self._check_auto_management(path_str, client.get("automatic_management_paths", ""))
        qbt_client.torrents_add(
            torrent_files=torrent.dump(),
            save_path=path_str,
            use_auto_torrent_management=auto_management,
            is_skip_checking=True,
            content_layout=client.get("content_layout", "Original"),
            category=meta.get("qbit_cat", client.get("qbit_cat")),
        )

        await self._wait_for_torrent(qbt_client, torrent.infohash)
        self._apply_qbit_tags(qbt_client, torrent.infohash, client, meta, tracker)
        console.print(f"Added to: {path_str}")

    async def _add_to_deluge(
        self,
        save_path: Path,
        torrent_path: Path,
        torrent: Torrent,
        meta: dict,
        local_path: str,
        remote_path: str,
        client: dict,
        tracker: str,
    ) -> None:
        """Add torrent to Deluge."""
        path_str = self._adjust_path(save_path, local_path, remote_path)
        client = DelugeRPCClient(client["deluge_url"], int(client["deluge_port"]), client["deluge_user"], client["deluge_pass"])
        client.connect()

        if client.connected:
            console.print("Connected to Deluge")
            client.call(
                "core.add_torrent_file",
                str(torrent_path),
                base64.b64encode(torrent.dump()),
                {"download_location": path_str, "seed_mode": True},
            )
            if meta.get("debug"):
                console.print(f"[cyan]Path: {path_str}")
        else:
            console.print("[bold red]Unable to connect to Deluge")

    async def _add_to_transmission(
        self,
        save_path: Path,
        torrent_path: Path,
        torrent: Torrent,
        meta: dict,
        local_path: str,
        remote_path: str,
        client: dict,
        tracker: str,
    ) -> None:
        """Add torrent to Transmission."""
        path_str = self._adjust_path(save_path, local_path, remote_path)
        if len(meta["filelist"]) == 1 and not save_path.is_dir():
            path_str = str(save_path.parent)

        async with httpx.AsyncClient(auth=(client.get("transmission_user"), client.get("transmission_pass"))) as http_client:
            with torrent_path.open("rb") as f:
                torrent_data = f.read()
                if not torrent_data:
                    console.print("[bold red]Torrent data is empty or unreadable")
                    return

            data = {"metainfo": base64.b64encode(torrent_data).decode("ascii"), "download-dir": path_str}
            result = await self.transmission_rpc_call(http_client, "torrent-add", data)
            console.print(
                "[bold green]Torrent added to Transmission"
                if result and result["result"] == "success"
                else f"[bold red]Error from Transmission: {result}"
            )

    async def _add_to_watch_folder(
        self,
        save_path: Path,
        torrent_path: Path,
        torrent: Torrent,
        meta: dict,
        local_path: str,
        remote_path: str,
        client: dict,
        tracker: str,
    ) -> None:
        """Copy torrent to watch folder."""
        shutil.copy(torrent_path, Path(client["watch_folder"]))

    def add_fast_resume(self, metainfo: dict, datapath: str, torrent: Torrent) -> dict:
        """Add fast resume data to a metafile dict."""
        files = metainfo["info"].get("files")
        single = files is None
        if single:
            datapath = str(Path(datapath) / metainfo["info"]["name"]) if Path(datapath).is_dir() else datapath
            files = [Bunch(path=[datapath], length=metainfo["info"]["length"])]

        resume = metainfo.setdefault("libtorrent_resume", {})
        resume["bitfield"] = len(metainfo["info"]["pieces"]) // 20
        resume["files"] = []
        piece_length = metainfo["info"]["piece length"]
        offset = 0

        for fileinfo in files:
            filepath = Path(datapath) / Path(*fileinfo["path"]) if not single else Path(fileinfo["path"][0])
            if filepath.stat().st_size != fileinfo["length"]:
                raise OSError(
                    errno.EINVAL, f"File size mismatch for {filepath} [is {filepath.stat().st_size}, expected {fileinfo['length']}]"
                )

            resume["files"].append({
                "priority": 1,
                "mtime": int(filepath.stat().st_mtime),
                "completed": (offset + fileinfo["length"] + piece_length - 1) // piece_length - offset // piece_length,
            })
            offset += fileinfo["length"]
        return metainfo

    async def transmission_rpc_call(self, client: httpx.AsyncClient, method: str, arguments: dict = None) -> Optional[dict]:
        """Execute an RPC call to Transmission."""
        url = f"{self.config['TORRENT_CLIENTS']['transmission']['transmission_url']}/transmission/rpc"
        data = {"method": method, "arguments": arguments or {}}

        async with client.post(url, json=data) as response:
            if response.status_code == 409:
                session_id = response.headers.get("X-Transmission-Session-Id")
                if session_id:
                    async with client.post(url, json=data, headers={"X-Transmission-Session-Id": session_id}) as resp:
                        return resp.json()
            elif response.status_code == 200:
                return resp.json()
            console.print(f"[bold red]Failed to execute RPC: HTTP {response.status_code}")
            return None

    def _get_torrent_client(self, meta: dict) -> Optional[str]:
        """Determine the torrent client from meta or config."""
        return meta.get("client") or self.config["DEFAULT"]["default_torrent_client"]

    async def _get_valid_torrenthash(self, meta: dict, torrent_storage_dir: Path, client_type: str, client: dict) -> Optional[str]:
        """Check for a valid torrent hash in storage or client."""
        for key in ("torrenthash", "ext_torrenthash"):
            if meta.get(key):
                valid, _ = await self.is_valid_torrent(
                    meta, torrent_storage_dir / f"{meta[key]}.torrent", meta[key], client_type, print_err=True
                )
                if valid:
                    log.debug(f"Valid torrent hash found: {meta[key]}")
                    return meta[key]

        if client_type == "qbit" and client.get("enable_search"):
            return await self.search_qbit_for_torrent(meta, client)
        if client_type == "transmission" and client.get("enable_search"):
            return await self.search_transmission_for_torrent(meta, client)
        console.print("[bold yellow]No valid .torrent found")
        return None

    def _validate_torrent_properties(self, torrent: Torrent, torrenthash: str, wrong_file: bool) -> Tuple[bool, str]:
        """Validate torrent properties like piece size and count."""
        log.debug(f"Validating torrent properties: pieces={torrent.pieces}, piece_size={torrent.piece_size}")
        if wrong_file:
            return False, "[bold red]Provided .torrent has unexpected files"
        if (torrent.pieces >= 7000 and torrent.piece_size < 8388608) or (torrent.pieces >= 4000 and torrent.piece_size < 4194304):
            return False, "[bold yellow]Too many pieces exist in current hash. REHASHING"
        if torrent.piece_size < 32768:
            return False, "[bold yellow]Piece size too small to reuse"
        return True, f"[bold green]REUSING .torrent with infohash: [bold yellow]{torrenthash}"

    def _adjust_path(self, save_path: Path, local_path: str, remote_path: str) -> str:
        """Adjust save path for remote mapping."""
        path_str = str(save_path)
        if local_path.lower() in path_str.lower() and local_path != remote_path:
            path_str = path_str.replace(local_path, remote_path).replace("\\", "/")
        return path_str

    def _check_auto_management(self, path: str, am_config: str) -> bool:
        """Check if path qualifies for qBittorrent auto-management."""
        if isinstance(am_config, list):
            return any(Path(each).as_posix().lower() in path.lower() for each in am_config)
        return bool(am_config.strip() and Path(am_config).as_posix().lower() in path.lower())

    async def _wait_for_torrent(self, qbt_client: qbittorrentapi.Client, infohash: str) -> None:
        """Wait for qBittorrent to recognize the added torrent."""
        for _ in range(30):
            if qbt_client.torrents_info(torrent_hashes=infohash):
                qbt_client.torrents_resume(infohash)
                return
            await asyncio.sleep(1)
        console.print(f"[bold red]Warning: Torrent with hash {infohash} not found after adding.")

    def _apply_qbit_tags(self, qbt_client: qbittorrentapi.Client, infohash: str, client: dict, meta: dict, tracker: str) -> None:
        """Apply tags to a qBittorrent torrent with corrected tracker tag retrieval."""
        tags = set()
        if client.get("qbit_tag"):
            tags.add(client["qbit_tag"])
        if meta.get("qbit_tag"):
            tags.add(meta["qbit_tag"])
        tracker_tag = self.config["TRACKERS"].get(tracker, {}).get("qbit_tag")
        if tracker_tag:
            tags.add(tracker_tag)

        if tags:
            qbt_client.torrents_add_tags(tags=", ".join(tags), torrent_hashes=infohash)
