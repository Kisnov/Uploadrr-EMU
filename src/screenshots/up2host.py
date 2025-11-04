import asyncio
from importlib import import_module
from pathlib import Path
from typing import Dict, List

import httpx

from src.progress import ProgressManager
from src.utils.console import console, log


class ScreenshotUploader:
    def __init__(self, config, base_dir):
        self.config = config
        self.base_dir = base_dir
        self.timeout = httpx.Timeout(60.0)
        self.client = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()

    def _load_host(self, host_name: str):
        """Dynamically load and instantiate an image host plugin."""
        module_name = host_name.split(".")[0]
        class_name = module_name.upper()

        try:
            module = import_module(f"src.screenshots.imghosts.{module_name}")
            host_class = getattr(module, class_name)
            return host_class(self.config)
        except (ImportError, AttributeError, KeyError) as e:
            log.warning(f"[Uploader] Failed to load host '{module_name}'): {e}")
            return None

    async def upload_screenshots(
        self, meta: Dict, screens: Dict, viable_hosts: List[str], tracker_whitelists: Dict[str, List[str]], task_widget=None
    ) -> Dict:
        async with self:
            pm = ProgressManager.get_instance()
            if pm:
                total_to_upload = 0
                if "main" in screens:
                    total_to_upload += min(len(screens["main"]), meta.get("screens", 3))
                if "episodes" in screens:
                    per_episode = self.config["SCREENS"].get("per_episode", 3)
                    for ep_screens in screens["episodes"].values():
                        total_to_upload += min(len(ep_screens), per_episode)
                if "contact_sheets" in screens:
                    contact_images = (
                        screens["contact_sheets"] if isinstance(screens["contact_sheets"], list) else [screens["contact_sheets"]]
                    )
                    total_to_upload += len(contact_images)
                pm.screen_start(total_to_upload)

            result = {"main": [], "episodes": {}, "contact_sheets": []}

            for section, images in screens.items():
                if section == "contact_sheets" and images:
                    images = [
                        {"local_file": img} if isinstance(img, str) else img
                        for img in (images if isinstance(images, list) else [images])
                    ]

                if section == "main":
                    max_images = len(screens) if meta.get("is_music", False) else meta.get("screens", 3)
                    uploaded = await self._upload_batch(meta, images, viable_hosts, meta["name"], max_images, meta["xxx"])
                    for i, up in enumerate(uploaded):
                        up["local_file"] = images[i]["local_file"]
                    result["main"] = uploaded

                elif section == "episodes":
                    per_episode = self.config["SCREENS"].get("per_episode", 3)
                    for ep_name, ep_screens in images.items():
                        uploaded = await self._upload_batch(
                            meta, ep_screens, viable_hosts, f"{ep_name} - {meta['name']}", per_episode, meta["xxx"]
                        )
                        for i, up in enumerate(uploaded):
                            up["local_file"] = ep_screens[i]["local_file"]
                        result["episodes"][ep_name] = uploaded

                elif section == "contact_sheets":
                    uploaded = await self._upload_batch(meta, images, viable_hosts, f"CS - {meta['name']}", len(images), meta["xxx"])
                    for i, up in enumerate(uploaded):
                        up["local_file"] = images[i]["local_file"]
                    result["contact_sheets"] = uploaded

            if pm:
                pm.screen_done()
            return result

    async def _upload_batch(self, meta, images, viable_hosts, label, max_images, adult=False):
        # Ensure images is a list of dicts
        images = [{"local_file": img} if isinstance(img, str) else img for img in (images if isinstance(images, list) else [images])]
        if not images:
            log.debug(f"No images to upload for {label}")
            return []

        if meta.get("is_music", False):
            image_paths = []
            for img in images:
                if "local_file" in img:
                    img_path = Path(img["local_file"])
                    resolved_path = img_path.resolve()
                    image_paths.append(str(resolved_path))
                    if not resolved_path.exists():
                        log.warning(f"Music image file does not exist: {img['local_file']}")
        else:
            tmp_dir = self.base_dir / "tmp" / meta["uuid"]
            image_paths = [
                (tmp_dir / img["local_file"]).as_posix()
                for img in images[:max_images]
                if "local_file" in img and Path(tmp_dir / img["local_file"]).exists()
            ]

        if not image_paths:
            log.warning(f"No valid image paths found for {label}")
            return []

        last_exc = None
        if adult:
            adult_cfg = self.config.get("ADULT", {})
            adult_keys = adult_cfg.get("API_KEYS", {})
            adult_host_only = adult_cfg.get("adult_host_only", True)
            imgbox_only = adult_cfg.get("imgbox_only", False)

            # Build allowed hosts
            allowed = set()
            for host in viable_hosts:
                if host in adult_keys:
                    allowed.add(host)
                elif not adult_host_only:
                    # Allow fallback to SCREENS if host has key there
                    if host in self.config["SCREENS"]["API_KEYS"]:
                        allowed.add(host)
                elif imgbox_only and host == "imgbox" and "imgbox" in self.config["SCREENS"]["API_KEYS"]:
                    allowed.add(host)

            viable_hosts = [h for h in viable_hosts if h in allowed]

            if not viable_hosts:
                log.warning(f"No viable adult hosts found for {label}")
                return []
        else:
            screen_keys = self.config["SCREENS"]["API_KEYS"]
            viable_hosts = [h for h in viable_hosts if h in screen_keys]

        for host in viable_hosts:
            try:
                uploaded = await self._upload_with_retries(host, images, image_paths, meta, label, max_images, adult=adult)
                if uploaded:
                    return uploaded
                else:
                    log.warning(f"No images uploaded to {host} for {label}, retrying next host")
            except Exception as e:
                console.print_exception()
                last_exc = e
                log.error(f"[Uploader] {host} failed batch for {label}: {e!r}", exc_info=True)
        err_msg = f"Failed to upload {label} to any host"
        if last_exc:
            err_msg += f": last error: {last_exc!r}"
        log.error(err_msg)
        return []

    async def _upload_with_retries(self, host_name, images, image_paths, meta, title, max_images, adult=False, max_retries=3):
        pm = ProgressManager.get_instance()
        host = self._load_host(host_name)
        if not host:
            log.error(f"[Uploader] Skipping upload: could not load host '{host_name}' (adult={adult})")
            return []

        if not image_paths:
            log.warning(f"No valid image paths for upload to {host_name}")
            return []

        for attempt in range(max_retries):
            try:
                results = []
                if hasattr(host, "upload_batch"):
                    batch_results = await host.upload_batch(self.client, image_paths, title=title, adult=adult)
                    successful = sum(
                        1
                        for r in batch_results
                        if r and isinstance(r, dict) and any(r.get(k) for k in ("img_url", "raw_url", "web_url"))
                    )
                    if pm:
                        pm.screen_up(successful)
                    results = batch_results
                else:
                    tasks = [host.upload(self.client, path, title=title, adult=adult) for path in image_paths]
                    for f in asyncio.as_completed(tasks):
                        try:
                            r = await f
                            log.debug(f"Raw single result from {host_name}: {r}")
                            if r and isinstance(r, dict) and any(r.get(k) for k in ("img_url", "raw_url", "web_url")) and pm:
                                pm.screen_up(1)
                            results.append(r)
                        except Exception as e:
                            log.debug(f"Upload failed: {e}")
                            results.append(None)

                # Filter valid results
                valid_results = []
                for r, img in zip(results, images[:max_images]):
                    if not r:
                        continue
                    if not isinstance(r, dict):
                        continue
                    if not any(r.get(k) for k in ("img_url", "raw_url", "web_url")):
                        continue
                    result = {
                        "img_url": r.get("img_url", ""),
                        "raw_url": r.get("raw_url", ""),
                        "web_url": r.get("web_url", ""),
                        "local_file": img["local_file"],
                        "ss_time": img.get("ss_time", 0),
                    }
                    valid_results.append(result)
                if valid_results:
                    return valid_results
                else:
                    log.warning(f"No valid uploads to {host_name} on attempt {attempt + 1}, retrying")

            except Exception as e:
                log.error(f"{host_name} retry {attempt + 1}/{max_retries} failed: {e!r}")
                console.print_exception()
                await asyncio.sleep(0.3)

        log.error(f"All retries failed for host {host_name}, returning partial or no results")
        return []  # Return empty list to indicate total failure
