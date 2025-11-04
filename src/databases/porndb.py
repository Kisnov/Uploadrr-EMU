import io
import os
import re
import struct
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import httpx
from PIL import Image
from rich.prompt import Prompt

from src.progress import ProgressManager
from src.utils.console import log
from src.utils.phash import PerceptualHash
from src.utils.trailer import trailer_to_webp


class PornDbHandler:
    def __init__(self, config: Dict[str, str], mihandle):
        self.config = config
        self.apikey = config.get("ADULT", {}).get("API_KEYS", {}).get("porndb")
        self.use_phash = config.get("ADULT", {}).get("use_phash", False)
        self.mi_handler = mihandle

    async def get_porndb_info(self, meta, config) -> Tuple[Dict[str, str], str]:
        if not self.apikey:
            log.error("API key 'porndb' not found in config['ADULT']")
            return {}, ""
        if meta.get("xmovie"):
            endpoint = "movies"
        elif meta.get("jav"):
            endpoint = "jav"
        else:
            endpoint = "scenes"

        try:
            mi = await self.mi_handler.load(meta["uuid"], meta["path"], meta["is_dir"])
            general_track = next((t for t in mi.get("media", {}).get("track", []) if t.get("@type") == "General"), None)
            duration = float(general_track.get("Duration", 0)) if general_track else 300.0
        except Exception as e:
            log.warning(f"Failed to extract duration from MediaInfo for {meta['uuid']}: {e}")
            duration = 300.0  # 5-minute fallback
            log.info(f"Falling back to default duration: {duration}s")
        log.debug(f"DURATION = {duration}")
        normalized = re.sub(r"[^a-zA-Z0-9]+", ".", meta["uuid"]).strip(".")
        filename = meta["filename"]
        scene_id = None
        id = None if not meta.get("pdb_manual") else meta["pdb_manual"]

        if id:
            try:
                uuid.UUID(id)
                scene_id = id
            except ValueError:
                if id.isdigit():
                    scene_id = id
                else:
                    normalized = id

        async with httpx.AsyncClient() as client:
            match_method, scenes = await self._search_porndb(client, endpoint, scene_id, normalized, filename, meta, duration)
            log.debug(f"Scenes returned: {scenes}")
            if not scenes:
                log.warning("No valid scenes found, returning empty data.")
                return {}, ""

            # For manual ID or created, scenes may contain a single dict; otherwise, it's a list
            selected = None
            if scene_id or match_method == "created":
                selected = scenes[0] if scenes else None
            else:
                for scene in scenes:
                    api_duration = scene.get("duration", 0)
                    if abs(api_duration - duration) < 10:
                        selected = scene
                        break
                if not selected and scenes:
                    selected = scenes[0]

            if not selected:
                log.error("No scene selected after search.")
                return {}, ""

            log.debug(f"Selected scene: {selected}")
            desc_text = selected.get("description", "")
            performers = [p["name"] for p in selected.get("performers", [])]
            tags = [t["name"] for t in selected.get("tags", [])]
            sid = selected.get("id") or selected.get("_id", "")
            image_url = selected.get("image", "") or selected.get("front_cover", "")
            poster_url = selected.get("poster", "") or selected.get("poster_image", "")
            back_url = selected.get("back_image", "") or selected.get("background", {}).get("full", "")
            trailer = selected.get("trailer", "")
            title = selected.get("title", "")
            date = selected.get("date", "")
            year = date.split("-")[0] if date and "-" in date else ""
            url = selected.get("url", "")
            path = Path(meta["base_dir"]) / "tmp" / meta["uuid"]
            site_info = selected.get("site") or {}
            network_info = site_info.get("network") or {}
            parent_info = site_info.get("parent") or {}
            site = site_info.get("name", "").replace(" ", "")
            network = network_info.get("name", "").replace(" ", "")
            parent = parent_info.get("name", "").replace(" ", "")

            # Download images
            await self._download_image(client, image_url, path / "image.jpg")
            await self._download_image(client, poster_url, path / "cover.jpg")
            await self._download_image(client, back_url, path / "back.jpg")

            if config.get("ADULT", {}).get("add_trailer", False) and trailer and not meta.get("trailer"):
                mp4trailer = await self._download_video(client, trailer, path / "trailer.mp4")
                if mp4trailer and mp4trailer.exists():
                    meta["trailer"] = await trailer_to_webp(mp4trailer, path, config)

            porndb_data = {
                "title": title,
                "date": date,
                "year": year,
                "description": desc_text,
                "performers": ", ".join(performers) if performers else "",
                "tags": ", ".join(tags + performers) if tags or performers else "",
                "scene_id": sid,
                "image": image_url,
                "poster": poster_url,
                "back": back_url,
                "trailer": trailer,
                "url": url,
                "site": site,
                "network": network,
                "parent": parent,
            }
            log.info("Successfully Fetched ThePornDB data.")
            return porndb_data, title

    async def _download_image(self, client, url: str, local_path: Path) -> Optional[Path]:
        if not url:
            return None
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content

            img = Image.open(io.BytesIO(content))

            if img.format and img.format.upper() in ["JPEG", "JPG"]:
                local_path = local_path.with_suffix(".jpg")
                with open(local_path, "wb") as f:
                    f.write(content)
            else:
                img = img.convert("RGB")
                local_path = local_path.with_suffix(".jpg")
                img.save(local_path, format="JPEG")

            return local_path
        except Exception as e:
            log.warning(f"Failed to download image {url}: {e}")
            return None

    async def _download_video(
        self,
        client,
        url: str,
        local_path: Path,
        *,
        chunk_size: int = 1024 * 256,  # 256 KiB
    ) -> Optional[Path]:
        """Stream-download a video file and save it to *local_path*."""
        if not url:
            return None

        try:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                local_path.parent.mkdir(parents=True, exist_ok=True)
                with local_path.open("wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size):
                        f.write(chunk)
            log.debug(f"Downloaded trailer to {local_path}")
            return local_path
        except Exception as e:
            log.warning(f"Failed to download trailer {url}: {e}")
            return None

    def compute_oshash(self, filepath: str) -> str:
        """Compute standard OSHASH for a video file."""
        CHUNK_SIZE = 65536  # 64KB
        CHUNK_BYTE_FORMAT = "<q"  # Little-endian signed 64-bit int
        BYTE_SIZE = struct.calcsize(CHUNK_BYTE_FORMAT)
        MIN_FILE_SIZE = CHUNK_SIZE * 2  # 128KB minimum

        path = Path(filepath)
        try:
            file_size = path.stat().st_size
            if file_size < MIN_FILE_SIZE:
                log.warning(f"File too small for OSHASH: {file_size} bytes (min {MIN_FILE_SIZE})")
                return ""

            file_hash = file_size  # Start with file size as int

            with path.open("rb") as f:
                for _ in range(CHUNK_SIZE // BYTE_SIZE):
                    file_bytes = f.read(BYTE_SIZE)
                    if len(file_bytes) != BYTE_SIZE:
                        raise ValueError("Incomplete read from file head")
                    (unpacked,) = struct.unpack(CHUNK_BYTE_FORMAT, file_bytes)
                    file_hash += unpacked
                    file_hash &= 0xFFFFFFFFFFFFFFFF  # Wrap to unsigned 64-bit

                f.seek(-CHUNK_SIZE, os.SEEK_END)
                for _ in range(CHUNK_SIZE // BYTE_SIZE):
                    file_bytes = f.read(BYTE_SIZE)
                    if len(file_bytes) != BYTE_SIZE:
                        raise ValueError("Incomplete read from file tail")
                    (unpacked,) = struct.unpack(CHUNK_BYTE_FORMAT, file_bytes)
                    file_hash += unpacked
                    file_hash &= 0xFFFFFFFFFFFFFFFF  # Wrap to unsigned 64-bit

            return f"{file_hash:016x}"

        except Exception as e:
            log.warning(f"Failed to compute OSHASH: {e}")
            return ""

    async def _compute_hashes(self, filepath: str, duration: float, meta: Dict) -> Dict[str, str]:
        """Compute or retrieve OSHASH and PHASH (if enabled) and store in meta."""
        hashes = {}
        # Validate OSHASH
        if (
            meta.get("oshash")
            and isinstance(meta["oshash"], str)
            and len(meta["oshash"]) == 16
            and all(c in "0123456789abcdef" for c in meta["oshash"])
        ):
            hashes["oshash"] = meta["oshash"]
        else:
            oshash = self.compute_oshash(filepath)
            if oshash and isinstance(oshash, str) and len(oshash) == 16 and all(c in "0123456789abcdef" for c in oshash):
                hashes["oshash"] = oshash
                meta["oshash"] = oshash

        # Validate PHASH
        if self.use_phash:
            if (
                meta.get("phash")
                and isinstance(meta["phash"], str)
                and len(meta["phash"]) > 0
                and all(c in "0123456789abcdef" for c in meta["phash"])
            ):
                hashes["phash"] = meta["phash"]
            else:
                hasher = PerceptualHash()
                phash = hasher.compute_phash(filepath, duration)
                if phash and isinstance(phash, str) and len(phash) > 0 and all(c in "0123456789abcdef" for c in phash):
                    hashes["phash"] = phash
                    meta["phash"] = phash
        return hashes

    async def _submit_hashes(
        self, client, endpoint: str, scene_id: str, hashes: Dict[str, str], filename: str, duration: float, meta: Dict
    ):
        """Submit OSHASH and/or PHASH to ThePornDB for a confirmed match using POST if not already submitted."""
        headers = {"Authorization": f"Bearer {self.apikey}", "Content-Type": "application/json"}
        for hash_type, hash_value in hashes.items():
            if not hash_value or not isinstance(hash_value, str) or not all(c in "0123456789abcdef" for c in hash_value):
                log.warning(f"Invalid {hash_type} hash for {filename}, skipping submission.")
                continue
            if meta.get(f"{hash_type}_submitted"):
                log.debug(f"Skipping {hash_type} submission for {filename} as it was already submitted.")
                continue
            url = f"https://api.theporndb.net/{endpoint}/{scene_id}/hash"
            payload = {"hash": hash_value, "type": hash_type.upper(), "duration": int(duration)}
            try:
                log.debug(f"Submitting {hash_type} for scene {scene_id}: {url} with payload {payload}")
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                log.info(f"Successfully submitted {hash_type} for {filename} with scene ID {scene_id}")
                meta[f"{hash_type}_submitted"] = True
            except Exception as e:
                log.warning(f"Failed to submit {hash_type} for {filename}: {e}")

    async def _search_porndb(
        self, client, endpoint: str, scene_id: str, normalized: str, filename: str, meta: Dict, duration: float
    ) -> Tuple[str, List[Dict]]:  # Returns (match_method, scenes)
        headers = {"Authorization": f"Bearer {self.apikey}"}
        category_param = "&category_id=1" if endpoint == "scenes" else ""

        # Compute and store hashes
        hashes = await self._compute_hashes(meta["path"], duration, meta)

        # Try scene_id first if provided (manual direct ID)
        if scene_id:
            url = f"https://api.theporndb.net/{endpoint}/{scene_id}"
            try:
                log.debug(f"Trying: [blue]{url}")
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                scenes = [data["data"]] if "data" in data else []
                if scenes:
                    # Submit hashes since manual ID
                    await self._submit_hashes(client, endpoint, scene_id, hashes, filename, duration, meta)
                    return "manual_id", scenes
            except Exception as e:
                log.error(f"Failed to fetch with scene ID: {e}")
            return "none", []

        endpoints = [endpoint, "jav", "movies", "scenes"]

        # Try PHASH if enabled
        if self.use_phash and hashes.get("phash"):
            for ep in endpoints:
                url_phash = f"https://api.theporndb.net/{ep}?hash={hashes['phash']}&hashType=PHASH"
                try:
                    log.debug(f"Trying PHASH with endpoint '{ep}': {url_phash}")
                    resp = await client.get(url_phash, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    scenes = data.get("data", [])
                    if scenes:
                        log.info(f"Match found via PHASH with endpoint '{ep}' for {filename}")
                        # Submit hashes (including oshash if available)
                        await self._submit_hashes(
                            client, ep, scenes[0].get("id") or scenes[0].get("_id", ""), hashes, filename, duration, meta
                        )
                        return "phash", scenes
                except Exception as e:
                    log.debug(f"PHASH search with endpoint '{ep}' failed: {e}")

        # Try OSHASH
        if hashes.get("oshash"):
            for ep in endpoints:
                url_oshash = f"https://api.theporndb.net/{ep}?hash={hashes['oshash']}&hashType=OSHASH"
                try:
                    log.debug(f"Trying OSHASH with endpoint '{ep}': {url_oshash}")
                    resp = await client.get(url_oshash, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    scenes = data.get("data", [])
                    if scenes:
                        log.info(f"Match found via OSHASH with endpoint '{ep}' for {filename}")
                        # Submit hashes (including phash if available)
                        await self._submit_hashes(
                            client, ep, scenes[0].get("id") or scenes[0].get("_id", ""), hashes, filename, duration, meta
                        )
                        return "oshash", scenes
                except Exception as e:
                    log.debug(f"OSHASH search with endpoint '{ep}' failed: {e}")

        # Try normalized filename
        url = f"https://api.theporndb.net/{endpoint}?parse={normalized}{category_param}&per_page=20&page=1"
        url2 = f"https://api.theporndb.net/{endpoint}?parse={quote_plus(filename)}{category_param}&per_page=20&page=1"
        try:
            log.debug(f"Trying: [blue]{url}")
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            scenes = data.get("data", [])
            if scenes:
                return "filename", scenes
            log.debug(f"Trying: [blue]{url2}")
            resp = await client.get(url2, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            scenes = data.get("data", [])
            if scenes:
                return "filename", scenes
        except Exception as e:
            log.error(f"Failed to fetch by name: {e}")

        # No match, handle prompt or unattended
        if meta.get("unattended"):
            if not self.use_phash:
                log.info("Reminder: Enabling use_phash in config may improve matching success.")
            log.warning("Skipping in unattended mode.")
            return "none", []

        progress = ProgressManager.get_instance()
        while True:
            with progress.pause_for_interaction():
                log.info(f"Unable to find match for [bright_magenta]{filename}[/bright_magenta] in [bright_cyan]{endpoint}")
                prompt_text = "Please enter ThePornDB ID (ID or uuid, else skip) "
                if not self.use_phash:
                    prompt_text = "Please enter ThePornDB ID (ID or uuid, 'phash' to compute and try PHASH, else skip)"
                input_id = Prompt.ask(prompt_text).strip().lower()
            if not input_id or input_id == "skip":
                log.warning("No database info provided.")
                return "none", []

            if input_id == "phash" and not self.use_phash:
                if meta.get("phash"):
                    log.warning("PHASH already computed, cannot recompute.")
                    continue
                # Compute phash on demand
                hasher = PerceptualHash()
                phash = hasher.compute_phash(meta["path"], duration)
                if phash:
                    hashes["phash"] = phash
                    meta["phash"] = phash
                    # Now try PHASH search
                    for ep in endpoints:
                        url_phash = f"https://api.theporndb.net/{ep}?hash={phash}&hashType=PHASH"
                        try:
                            log.debug(f"Trying on-demand PHASH with endpoint '{ep}': {url_phash}")
                            resp = await client.get(url_phash, headers=headers)
                            resp.raise_for_status()
                            data = resp.json()
                            scenes = data.get("data", [])
                            if scenes:
                                log.info(f"Match found via on-demand PHASH with endpoint '{ep}' for {filename}")
                                # Submit hashes
                                await self._submit_hashes(
                                    client, ep, scenes[0].get("id") or scenes[0].get("_id", ""), hashes, filename, duration, meta
                                )
                                return "phash", scenes
                        except Exception as e:
                            log.debug(f"On-demand PHASH search with endpoint '{ep}' failed: {e}")
                    log.warning("No match found even with on-demand PHASH.")
                else:
                    log.warning("Failed to compute PHASH.")
                continue  # Back to prompt
            try:
                if uuid.UUID(input_id) or input_id.isdigit():
                    url = f"https://api.theporndb.net/{endpoint}/{input_id}"
                    log.debug(f"Trying manual ID: [blue]{url}")
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    if not data or "data" not in data:
                        log.error("Empty or invalid response for provided ID.")
                        continue
                    log.debug(f"Received data: {data}")
                    # Submit hashes for confirmed match
                    await self._submit_hashes(client, endpoint, input_id, hashes, filename, duration, meta)
                    return "manual_id", [data["data"]]
                else:
                    log.error("Invalid ID format. Must be digits or UUID.")
            except httpx.HTTPStatusError as e:
                log.error(f"Failed to fetch: {e.response.status_code}")
            except ValueError:
                log.error("Invalid UUID format.")
            except Exception as e:
                log.error(f"Error fetching with provided ID: {e}")
        return "none", []
