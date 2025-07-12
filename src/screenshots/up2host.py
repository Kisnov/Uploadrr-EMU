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
        except (ImportError, AttributeError) as e:
            log.warning(f"[Uploader] Failed to load host '{module_name}': {e}")
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
                pm.screen_start(total_to_upload)

            result = {"main": [], "episodes": {}}

            for section, images in screens.items():
                if section == "main":
                    uploaded = await self._upload_batch(
                        meta, images, viable_hosts, f"main screenshots for {meta['name']}", meta.get("screens", 3)
                    )
                    log.debug(f"Uploaded {len(uploaded)} main images: {[img.get('img_url', '') for img in uploaded]}")
                    for i, up in enumerate(uploaded):
                        up["local_file"] = images[i]["local_file"]
                    result["main"] = uploaded

                elif section == "episodes":
                    per_episode = self.config["SCREENS"].get("per_episode", 3)
                    for ep_name, ep_screens in images.items():
                        uploaded = await self._upload_batch(
                            meta, ep_screens, viable_hosts, f"{ep_name} screenshots for {meta['name']}", per_episode
                        )
                        log.debug(
                            f"Uploaded {len(uploaded)} images for episode {ep_name}: {[img.get('img_url', '') for img in uploaded]}"
                        )
                        for i, up in enumerate(uploaded):
                            up["local_file"] = ep_screens[i]["local_file"]
                        result["episodes"][ep_name] = uploaded

            if pm:
                pm.screen_done()
            return result

    async def _upload_batch(self, meta, images, viable_hosts, label, max_images):
        log.debug(f"_upload_batch: {len(images)} images for {label}: {[img['local_file'] for img in images]}")
        if not images:
            log.debug(f"No images to upload for {label}")
            return []
        last_exc = None
        for host in viable_hosts:
            try:
                uploaded = await self._upload_with_retries(host, images, meta, meta["name"], max_images)
                if uploaded:
                    log.debug(f"Successfully uploaded {len(uploaded)} images to {host} for {label}")
                    return uploaded
                else:
                    log.debug(f"No images uploaded to {host} for {label}, retrying next host")
            except Exception as e:
                console.print_exception()
                last_exc = e
                log.debug(f"[Uploader] {host} failed batch for {label}: {e!r}", exc_info=True)
        err_msg = f"Failed to upload {label} to any host"
        if last_exc:
            err_msg += f": last error: {last_exc!r}"
        log.error(err_msg)
        return []

    async def _upload_with_retries(self, host_name, images, meta, title, max_images, max_retries=3):
        pm = ProgressManager.get_instance()
        host = self._load_host(host_name)
        if not host:
            log.error(f"[Uploader] Skipping upload: could not load host '{host_name}'")
            return []

        tmp_dir = self.base_dir / "tmp" / meta["uuid"]
        image_paths = [
            (tmp_dir / img["local_file"]).as_posix() for img in images[:max_images] if Path(tmp_dir / img["local_file"]).exists()
        ]
        log.debug(f"Preparing to upload {len(image_paths)} images: {image_paths}")

        for attempt in range(max_retries):
            try:
                results = []
                if hasattr(host, "upload_batch"):
                    batch_results = await host.upload_batch(self.client, image_paths, title=title)
                    successful = sum(1 for r in batch_results if r and all(k in r for k in ("img_url", "raw_url", "web_url")))
                    if pm:
                        pm.screen_up(successful)
                    results = batch_results
                else:
                    tasks = [host.upload(self.client, path, title) for path in image_paths]
                    for f in asyncio.as_completed(tasks):
                        try:
                            r = await f
                            if r and all(k in r for k in ("img_url", "raw_url", "web_url")) and pm:
                                pm.screen_up(1)
                            results.append(r)
                        except Exception as e:
                            log.debug(f"Upload failed: {e}")
                            results.append(None)

                # Filter valid results
                valid_results = [
                    {
                        "img_url": r["img_url"],
                        "raw_url": r["raw_url"],
                        "web_url": r["web_url"],
                        "local_file": img["local_file"],
                        "ss_time": img.get("ss_time", 0),
                    }
                    for r, img in zip(results, images[:max_images])
                    if r and all(k in r for k in ("img_url", "raw_url", "web_url"))
                ]

                if valid_results:
                    log.debug(f"Successfully uploaded {len(valid_results)} images to {host_name} on attempt {attempt + 1}")
                    return valid_results
                else:
                    log.debug(f"No valid uploads to {host_name} on attempt {attempt + 1}, retrying")

            except Exception as e:
                log.debug(f"{host_name} retry {attempt + 1}/{max_retries} failed: {e!r}")
                console.print_exception()
                await asyncio.sleep(0.3)

        log.error(f"All retries failed for host {host_name}, returning partial or no results")
        return []  # Return empty list to indicate total failure
