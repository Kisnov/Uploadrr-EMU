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
from src.utils.console import console, log
from src.utils.phash import PerceptualHash
from src.utils.trailer import download_trailer, trailer_to_webp


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

        endpoint = "movies" if meta.get("xmovie") else "jav" if meta.get("jav") else "scenes"

        # ── duration ─────────────────────────────────────────────────────────────
        try:
            mi = await self.mi_handler.load(meta["uuid"], meta["path"], meta["is_dir"])
            general_track = next((t for t in mi.get("media", {}).get("track", []) if t.get("@type") == "General"), None)
            duration = float(general_track.get("Duration", 0)) if general_track else 300.0
        except Exception as e:
            log.warning(f"Failed to extract duration from MediaInfo for {meta['uuid']}: {e}")
            duration = 300.0
        log.debug(f"DURATION = {duration}")

        # ── basic prep ───────────────────────────────────────────────────────────
        normalized = re.sub(r"[^a-zA-Z0-9]+", ".", meta["uuid"]).strip(".")
        filename = meta["filename"]
        scene_id = None
        if manual := meta.get("pdb_manual"):
            try:
                uuid.UUID(manual)
                scene_id = manual
            except ValueError:
                if manual.isdigit():
                    scene_id = manual
                else:
                    normalized = manual

        async with httpx.AsyncClient() as client:
            match_method, scenes, hashes = await self._search_porndb(client, endpoint, scene_id, normalized, filename, meta, duration)

            if not scenes:
                log.warning("No valid scenes found, returning empty data.")
                return {}, ""

            unattended = meta.get("unattended", False)
            search_year = None
            if (sy := meta.get("search_year")) and isinstance(sy, str) and sy.isdigit():
                search_year = int(sy)

            # ── choose the correct scene ───────────────────────────────────────────
            if match_method in {"phash", "oshash", "manual_id"}:
                selected = scenes[0]
            else:
                good_year_cands, all_sorted = self._filter_and_sort_by_year(scenes, search_year, unattended)

                if unattended and not good_year_cands:
                    log.warning("No scene within ±1 year in unattended mode → skipping")
                    return {}, ""

                pool = good_year_cands or all_sorted
                if not pool:
                    return {}, ""

                best = None
                best_diff = float("inf")
                for scene in pool:
                    api_dur = scene.get("duration") or 0
                    diff = abs(api_dur - duration)
                    if diff < best_diff:
                        best_diff = diff
                        best = scene

                if best and best_diff <= 60:
                    selected = best
                else:
                    selected = pool[0]

                should_ask = len(good_year_cands) > 1 or (len(good_year_cands) == 0 and len(pool) > 1)

                if should_ask and not unattended:
                    with ProgressManager.get_instance().pause_for_interaction():
                        display_cands = all_sorted[:3]
                        selected = await self._interactive_select(client, display_cands, endpoint, meta, filename, hashes, duration)
                    if not selected:
                        return {}, ""

            if not selected:
                log.error("No scene selected after all filters.")
                return {}, ""

            # Submit hashes for filename-based matches
            if match_method == "filename" and hashes:
                sid = selected.get("id") or selected.get("_id", "")
                if sid:
                    await self._submit_hashes(client, endpoint, sid, hashes, filename, duration, meta)

            # ── extract data ───────────────────────────────────────────────────────
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

            # ── download images ───────────────────────────────────────────────────
            await self._download_image(client, image_url, path / "image.jpg")
            await self._download_image(client, poster_url, path / "cover.jpg")
            await self._download_image(client, back_url, path / "back.jpg")

            # ── trailer ───────────────────────────────────────────────────────────
            if (
                config.get("ADULT", {}).get("add_trailer", False)
                and trailer
                and not meta.get("trailer")
                and not meta.get("user_trailer")
            ):
                mp4trailer = await download_trailer(client, trailer, path / "trailer.mp4")
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

            log.info("Successfully fetched ThePornDB data.")
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
        self, client, endpoint: str, scene_id: Optional[str], normalized: str, filename: str, meta: Dict, duration: float
    ) -> Tuple[str, List[Dict], Dict[str, str]]:  # Returns (match_method, scenes, hashes)
        headers = {"Authorization": f"Bearer {self.apikey}"}
        category_param = "&category_id=1" if endpoint == "scenes" else ""
        hashes = {}
        # Compute hashes if not split/scene or pack
        if len(meta["video_files"]) == 1:
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
                    self._update_meta_endpoint_flags(meta, endpoint)
                    # Submit hashes since manual ID
                    await self._submit_hashes(client, endpoint, scene_id, hashes, filename, duration, meta)
                    return "manual_id", scenes, hashes
            except Exception as e:
                log.error(f"Failed to fetch with scene ID: {e}")
            return "none", [], hashes

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
                        self._update_meta_endpoint_flags(meta, ep)
                        # Submit hashes (including oshash if available)
                        await self._submit_hashes(
                            client, ep, scenes[0].get("id") or scenes[0].get("_id", ""), hashes, filename, duration, meta
                        )
                        return "phash", scenes, hashes
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
                        self._update_meta_endpoint_flags(meta, ep)
                        # Submit hashes (including phash if available)
                        await self._submit_hashes(
                            client, ep, scenes[0].get("id") or scenes[0].get("_id", ""), hashes, filename, duration, meta
                        )
                        return "oshash", scenes, hashes
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
                return "filename", scenes, hashes
            log.debug(f"Trying: [blue]{url2}")
            resp = await client.get(url2, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            scenes = data.get("data", [])
            if scenes:
                return "filename", scenes, hashes
        except Exception as e:
            log.error(f"Failed to fetch by name: {e}")

        # No match, handle prompt or unattended
        if meta.get("unattended"):
            if not self.use_phash:
                log.info("Reminder: Enabling use_phash in config may improve matching success.")
            log.warning("Skipping in unattended mode.")
            return "none", [], hashes

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
                return "none", [], hashes

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
                                self._update_meta_endpoint_flags(meta, ep)
                                # Submit hashes
                                await self._submit_hashes(
                                    client, ep, scenes[0].get("id") or scenes[0].get("_id", ""), hashes, filename, duration, meta
                                )
                                return "phash", scenes, hashes
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
                    self._update_meta_endpoint_flags(meta, endpoint)
                    # Submit hashes for confirmed match
                    await self._submit_hashes(client, endpoint, input_id, hashes, filename, duration, meta)
                    return "manual_id", [data["data"]], hashes
                else:
                    log.error("Invalid ID format. Must be digits or UUID.")
            except httpx.HTTPStatusError as e:
                log.error(f"Failed to fetch: {e.response.status_code}")
            except ValueError:
                log.error("Invalid UUID format.")
            except Exception as e:
                log.error(f"Error fetching with provided ID: {e}")
        return "none", [], hashes

    def _update_meta_endpoint_flags(self, meta: Dict, endpoint: str):
        meta["xscene"] = endpoint == "scenes"
        meta["jav"] = endpoint == "jav"
        meta["xmovie"] = endpoint == "movies"

    def _filter_and_sort_by_year(
        self, scenes: List[Dict], search_year: Optional[int], unattended: bool
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Returns (good_year_candidates, all_sorted_candidates)
        good_year_candidates = scenes with year diff <= 1 (or all if no search_year)
        """
        if not search_year:
            return scenes[:], scenes[:]

        scored = []
        for scene in scenes:
            diff = 9999
            date = scene.get("date", "")
            if date and "-" in date:
                try:
                    scene_year = int(date.split("-")[0])
                    diff = abs(scene_year - search_year)
                except ValueError:
                    pass
            scored.append((diff, scene))

        scored.sort(key=lambda x: x[0])
        all_sorted = [s for _, s in scored]
        good = [s for d, s in scored if d <= 1]

        if unattended:
            return (good if good else [], all_sorted)

        return good, all_sorted

    async def _interactive_select(
        self, client, candidates: List[Dict], endpoint: str, meta: Dict, filename: str, hashes: Dict[str, str], duration: float
    ) -> Optional[Dict]:
        console.print(f"\n[bold yellow]Multiple possible matches for[/] [bright_magenta]{filename}[/]")
        options = candidates[:3]
        if len(candidates) > 3:
            console.print(f"[dim]Showing top 3 of {len(candidates)} results (closest year first)[/]")

        for i, scene in enumerate(options, 1):
            title = (scene.get("title") or "Untitled")[:80]
            date = scene.get("date") or "????-??-??"
            year = date[:4]
            performers = ", ".join(p["name"] for p in scene.get("performers", [])[:4])
            if not performers:
                performers = "Solo"
            site = (scene.get("site") or {}).get("name", "Unknown")
            dur = scene.get("duration")
            dur_str = f"{int(dur // 60)}m" if dur else "?"
            sid = scene.get("id") or scene.get("_id", "") or "?"

            console.print(f"  [bold cyan]{i}[/] {title}  [{year}]  {performers}  @ {site}  ({dur_str})  ID: {sid}")

        while True:
            choice = Prompt.ask(
                f"\n[green]?[/] Select [1-{len(options)}] (Enter = 1), type a scene ID, or [red]s[/]kip", default="1"
            ).strip()

            if choice.lower() in {"s", "skip"}:
                log.info("Match skipped by user")
                return None

            # Valid numbered choice
            if choice.isdigit():
                num = int(choice)
                if 1 <= num <= len(options):
                    selected = options[num - 1]
                    sid = selected.get("id") or selected.get("_id", "")
                    if sid and hashes:
                        await self._submit_hashes(client, endpoint, sid, hashes, filename, duration, meta)
                    return selected

            # Anything else → treat as manual scene ID
            manual_id = choice.strip()
            if not manual_id:
                continue

            try:
                url = f"https://api.theporndb.net/{endpoint}/{manual_id}"
                resp = await client.get(url, headers={"Authorization": f"Bearer {self.apikey}"})
                resp.raise_for_status()
                data = resp.json().get("data")
                if data:
                    log.info(f"User-provided ID {manual_id} resolved successfully")
                    sid = data.get("id") or data.get("_id", "")
                    if sid and hashes:
                        await self._submit_hashes(client, endpoint, sid, hashes, filename, duration, meta)
                    return data
                else:
                    console.print("[red]ID not found[/]")
            except Exception as e:
                console.print(f"[red]Error fetching ID: {e}[/]")
