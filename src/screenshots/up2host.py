import asyncio
import importlib

from pathlib import Path
import httpx

from src.console import console, log

class ScreenshotUploader:
    def __init__(self, config, base_dir):
        self.config = config
        self.base_dir = base_dir
        self.min_image_size = 75000
        self.timeout = httpx.Timeout(60.0)
        self.client = None

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client and not self.client.is_closed:
            await self.client.aclose()

    def _load_host(self, host_name):
        """Dynamicaly load imghost modules"""
        module_name = host_name.split('.')[0] if '.' in host_name else host_name
        try:
            module = importlib.import_module(f"src.screenshots.imghosts.{module_name}")
            host_class = getattr(module, module_name.upper())
            return host_class(self.config)
        except (ImportError, AttributeError) as e:
            log.warning(f"Failed to load host '{module_name}': {e}")
            return None

    async def upload_screenshots(self, meta, screens, viable_hosts=None, rehost=False, task_widget=None):
        """Uploads screenshots to viable hosts"""
        max_retries = 3
        existing = meta.get('image_list', []) if not rehost else []
        screens_count = screens if isinstance(screens, int) else len(screens) or meta.get('screens', 3)

        if rehost and isinstance(screens, (list, tuple)):
            to_upload = screens
        elif isinstance(screens, (list, tuple)) and all(Path(s).is_file() for s in screens):
            to_upload = [str(Path(s)) for s in screens]
        else:
            tmp_dir = self.base_dir / "tmp" / meta['uuid']
            images = [p for p in tmp_dir.glob("*.png")]
            if len(existing) >= screens_count and not rehost:
                if task_widget:
                    task_widget.text = f"Processing {meta.get('filename', 'screenshots')}: Reusing existing uploads"
                else:
                    console.print(f"[green]Reusing {len(existing)} existing uploads[/green]")
                return existing
            to_upload = [
                str(img) for img in images
                if str(img) not in [e.get('local_file') for e in existing]
                and img.stat().st_size >= self.min_image_size
            ][:screens_count - len(existing)]

        if not to_upload:
            if task_widget:
                task_widget.text = f"Processing {meta.get('filename', 'screenshots')}: No new screenshots to upload"
            else:
                console.print("[yellow]No new screenshots to upload[/yellow]")
            return existing if not rehost else []

        async with self:
            if task_widget:
                task_widget.text = f"Processing {meta.get('filename', 'screenshots')}: Uploading screenshots..."
            else:
                console.print(f"[yellow]Uploading {len(to_upload)} screenshots...[/yellow]")

            for host in viable_hosts or []:
                if not host:
                    continue
                uploaded_images = await self._upload_with_retries(host, to_upload, max_retries)
                if uploaded_images:
                    if task_widget:
                        task_widget.text = f"Processing {meta.get('filename', 'screenshots')}: Uploaded to {host}"
                    else:
                        console.print(f"[green]Uploaded {len(uploaded_images)} screenshots to {host}[/green]")
                    return uploaded_images

            log.debug("All viable hosts failed")
            if task_widget:
                task_widget.text = f"Processing {meta.get('filename', 'screenshots')}: Upload failed"
            else:
                console.print("[red]Failed to upload screenshots to any host[/red]")
            return existing if not rehost else []

    async def _upload_with_retries(self, host_name, images, max_retries):
        host = self._load_host(host_name)
        if not host:
            return []
        for attempt in range(1, max_retries + 1):
            uploaded = await self._upload_to_host(host, images)
            if uploaded:
                return uploaded
            if attempt < max_retries:
                log.debug(f"Retrying '{host_name}' (attempt {attempt}/{max_retries})")
        return []

    async def _upload_to_host(self, host, images):
        tasks = [host.upload(self.client, img) for img in images]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        uploaded_images = []
        for result, img in zip(results, images):
            if isinstance(result, dict):
                uploaded_images.append({
                    'img_url': result['img_url'],
                    'raw_url': result['raw_url'],
                    'web_url': result['web_url'],
                    'local_file': img
                })
        return uploaded_images

    async def shutdown(self):
        if self.client and not self.client.is_closed:
            await self.client.aclose()