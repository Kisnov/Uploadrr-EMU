import asyncio
from importlib import import_module
from pathlib import Path
from typing import Dict, List

import httpx

from src.progress import ProgressManager
from src.utils.console import console, log


class AllHostsFailedError(RuntimeError):
    """Every configured image host failed for a batch — distinct from other
    exceptions caught in ScreenshotManager.handle_screenshots() (which stays
    best-effort for everything else, e.g. one corrupt local screenshot)."""


class ScreenshotUploader:
    def __init__(self, config, base_dir):
        self.config = config
        self.base_dir = base_dir
        self.timeout = httpx.Timeout(60.0)
        self.client = None
        self.failed_hosts = set()

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
                    result["main"] = uploaded

                elif section == "episodes":
                    per_episode = self.config["SCREENS"].get("per_episode", 3)
                    for ep_name, ep_screens in images.items():
                        uploaded = await self._upload_batch(
                            meta, ep_screens, viable_hosts, f"{ep_name} - {meta['name']}", per_episode, meta["xxx"]
                        )
                        result["episodes"][ep_name] = uploaded

                elif section == "contact_sheets":
                    uploaded = await self._upload_batch(meta, images, viable_hosts, f"CS - {meta['name']}", len(images), meta["xxx"])
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

        # Cada captura con su ruta: el resultado de cada una vuelve a su sitio aunque falle otra.
        if meta.get("is_music", False):
            pending = []
            for img in images:
                if "local_file" in img:
                    resolved_path = Path(img["local_file"]).resolve()
                    pending.append((img, str(resolved_path)))
                    if not resolved_path.exists():
                        log.warning(f"Music image file does not exist: {img['local_file']}")
        else:
            tmp_dir = self.base_dir / "tmp" / meta["uuid"]
            pending = [
                (img, (tmp_dir / img["local_file"]).as_posix())
                for img in images[:max_images]
                if "local_file" in img and Path(tmp_dir / img["local_file"]).exists()
            ]

        if not pending:
            if "CS -" not in label:
                log.warning(f"No valid image paths found for {label}")
            return []

        last_exc = None
        NO_API_KEY_HOSTS = {"imgbox", "pixhost", "fastpic"}
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
                    if host in self.config["SCREENS"]["API_KEYS"] or host in NO_API_KEY_HOSTS:
                        allowed.add(host)
                elif imgbox_only and host == "imgbox":
                    allowed.add(host)

            viable_hosts = [h for h in viable_hosts if h in allowed]

            if not viable_hosts:
                log.warning(f"No viable adult hosts found for {label}")
                return []
        else:
            screen_keys = self.config["SCREENS"]["API_KEYS"]
            viable_hosts = [h for h in viable_hosts if h in screen_keys or h in NO_API_KEY_HOSTS]

        # Las que fallan en un host se reintentan ahí y luego en el siguiente; las que ya subieron no se repiten.
        uploaded = {}
        # Un host que no subió nada en un lote pasa al final en los siguientes: sus timeouts no se repiten en cada capítulo.
        for host in sorted(viable_hosts, key=lambda h: h in self.failed_hosts):
            missing = [i for i in range(len(pending)) if i not in uploaded]
            if not missing:
                break
            done = {}
            try:
                done = await self._upload_with_retries(host, [pending[i] for i in missing], missing, label, adult=adult)
            except Exception as e:
                console.print_exception()
                last_exc = e
                log.error(f"[Uploader] {host} failed batch for {label}: {e!r}", exc_info=True)
            uploaded.update(done)
            if done:
                self.failed_hosts.discard(host)
            else:
                self.failed_hosts.add(host)

        if not uploaded:
            err_msg = f"Failed to upload {label} to any host"
            if last_exc:
                err_msg += f": last error: {last_exc!r}"
            log.error(err_msg)
            raise AllHostsFailedError(err_msg)
        if len(uploaded) < len(pending):
            log.warning(f"Only {len(uploaded)}/{len(pending)} screenshots uploaded for {label}")
        return [uploaded[i] for i in sorted(uploaded)]

    async def _upload_with_retries(self, host_name, pending, indexes, title, adult=False, max_retries=3):
        """Sube a un host las capturas pendientes; devuelve {índice: resultado} de las que han subido."""
        pm = ProgressManager.get_instance()
        host = self._load_host(host_name)
        if not host:
            log.error(f"[Uploader] Skipping upload: could not load host '{host_name}' (adult={adult})")
            return {}

        done = {}
        for attempt in range(max_retries):
            todo = [n for n in range(len(pending)) if indexes[n] not in done]
            if not todo:
                break
            try:
                paths = [pending[n][1] for n in todo]
                if host_name == "imgbox":
                    # imgbox solo devuelve las que suben, con el nombre del fichero.
                    by_name = {
                        Path(r["local_file"]).name: r for r in await host.upload_batch(self.client, paths, title=title, adult=adult)
                    }
                    results = [by_name.get(Path(p).name) for p in paths]
                else:
                    # Más de 6 a la vez y los hosts cortan la conexión antes de dar la URL.
                    limit = asyncio.Semaphore(6)

                    async def one(path):
                        async with limit:
                            return await host.upload(self.client, path, title=title, adult=adult)

                    results = await asyncio.gather(*(one(p) for p in paths), return_exceptions=True)
                for n, r in zip(todo, results):
                    if isinstance(r, dict) and any(r.get(k) for k in ("img_url", "raw_url", "web_url")):
                        img = pending[n][0]
                        done[indexes[n]] = {
                            "img_url": r.get("img_url", ""),
                            "raw_url": r.get("raw_url", ""),
                            "web_url": r.get("web_url", ""),
                            "local_file": img["local_file"],
                            "ss_time": img.get("ss_time", 0),
                        }
                        if pm:
                            pm.screen_up(1)
                    elif isinstance(r, Exception):
                        log.debug(f"{host_name} upload failed: {r!r}")
            except Exception as e:
                log.error(f"{host_name} retry {attempt + 1}/{max_retries} failed: {e!r}")
                console.print_exception()
            if len(done) < len(pending) and attempt < max_retries - 1:
                log.warning(f"{len(pending) - len(done)} screenshots missing on {host_name} after attempt {attempt + 1}, retrying")
                await asyncio.sleep(0.3)
        return done
