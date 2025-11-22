import aiofiles

from .base import BaseImageHost


class PIXHOST(BaseImageHost):
    async def upload(self, client, image_path, title=None, adult=False):
        async with aiofiles.open(image_path, "rb") as f:
            data = await f.read()

        files = {
            "content_type": (None, "0", "text/plain"),
            "max_th_size": (None, "350", "text/plain"),
            "img": (image_path, data, "image/png"),
        }
        resp = await client.post("https://api.pixhost.to/images", files=files)
        self._validate_response(resp, "pixhost")

        result = resp.json()
        if "th_url" in result:
            return {
                "img_url": result["th_url"],
                "raw_url": result["th_url"].replace("https://t", "https://img").replace("/thumbs/", "/images/"),
                "web_url": result["show_url"],
            }
        raise ValueError(f"pixhost upload failed: {result.get('error', 'Unknown error')}")

    async def upload_batch(self, client, image_paths, title=None, adult=False):
        import asyncio

        from src.utils.console import log

        max_concurrent = 6
        results = []
        for i in range(0, len(image_paths), max_concurrent):
            batch = image_paths[i : i + max_concurrent]
            tasks = [self.upload(client, path, title, adult) for path in batch]
            for f in asyncio.as_completed(tasks):
                try:
                    result = await f
                    if result and all(k in result for k in ("img_url", "raw_url", "web_url")):
                        results.append(result)
                    else:
                        log.warning(f"[pixhost] Invalid upload result: {result}")
                        results.append(None)
                except Exception as e:
                    log.error(f"[pixhost] Upload failed: {e}")
                    results.append(None)
        return results
