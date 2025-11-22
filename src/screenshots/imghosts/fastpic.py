import xml.etree.ElementTree as ET

import aiofiles

from .base import BaseImageHost


class FASTPIC(BaseImageHost):
    def __init__(self, config):
        super().__init__(config)
        self.api_url = "https://fastpic.org/upload?api=1"
        self.thumb_width = self.config["SCREENS"].get("size", "350")

    async def upload(self, client, image_path, title=None, adult=False):
        async with aiofiles.open(image_path, "rb") as f:
            data = await f.read()

        files = {
            "method": (None, "file", "text/plain"),
            "file1": (image_path, data, "image/png"),
            "check_thumb": (None, "no", "text/plain"),
            "uploading": (None, "1", "text/plain"),
            "orig_rotate": (None, "0", "text/plain"),
            "thumb_size": (None, str(self.thumb_width), "text/plain"),
        }

        resp = await client.post(self.api_url, files=files)
        self._validate_response(resp, "fastpic")

        root = ET.fromstring(resp.text)
        status = root.find("status")
        if status is not None and status.text == "ok":
            raw_url = root.find("imagepath").text
            img_url = root.find("thumbpath").text
            view_url = root.find("viewfullurl").text.replace("fastpic.org/view/", "fastpic.org/fullview/")
            return {"img_url": img_url, "raw_url": raw_url, "web_url": view_url}

        error = root.find("error")
        if error is not None:
            log.error(f"Fastpic upload failed: {error.text}")  # noqa: F821
        raise ValueError("Fastpic upload failed: Invalid response or error encountered")

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
                        log.warning(f"[fastpic] Invalid upload result: {result}")
                        results.append(None)
                except Exception as e:
                    log.error(f"[fastpic] Upload failed: {e}")
                    results.append(None)
        return results
