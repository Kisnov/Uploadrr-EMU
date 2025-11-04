import asyncio
import base64

import aiofiles

from src.utils.console import log

from .base import BaseImageHost


class CheveretoTemplate(BaseImageHost):
    def __init__(self, config, api_url, imghost):
        super().__init__(config)
        self.api_url = api_url
        self.host = imghost

    async def upload(self, client, image_path, title=None, adult=False):
        """Upload an image to Chevereto and return a dict with img_url, raw_url, and web_url."""
        if adult:
            adult_cfg = self.config.get("ADULT", {})
            adult_keys = adult_cfg.get("API_KEYS", {})
            adult_host_only = adult_cfg.get("adult_host_only", True)

            if self.host in adult_keys:
                api_key = adult_keys[self.host]
            elif not adult_host_only and self.host in self.config["SCREENS"]["API_KEYS"]:
                api_key = self.config["SCREENS"]["API_KEYS"][self.host]

            elif adult_cfg.get("imgbox_only", False) and self.host == "imgbox":
                api_key = self.config["SCREENS"]["API_KEYS"].get("imgbox")
            else:
                raise KeyError(f"Host '{self.host}' not allowed for adult upload")
        else:
            api_key = self.config["SCREENS"]["API_KEYS"].get(self.host)
            if not api_key:
                raise KeyError(f"API key for '{self.host}' not found in SCREENS")

        if not api_key:
            raise KeyError(f"API key for '{self.host}' missing")

        log.debug(f"Using API key for {self.host} (adult={adult}): {api_key}")
        try:
            async with aiofiles.open(image_path, "rb") as f:
                img = await f.read()

            headers = {"X-API-Key": api_key}
            data = {"image": base64.b64encode(img).decode("utf8"), "nsfw": "1" if adult else "0"}
            if title:
                data["title"] = title

            resp = await client.post(self.api_url, headers=headers, data=data)

            content_type = resp.headers.get("content-type")
            text = await resp.aread()

            log.ddebug(f"[{self.host}] HTTP {resp.status_code} {resp.reason_phrase}")
            log.ddebug(f"[{self.host}] Content-Type: {content_type}")
            log.ddebug(f"[{self.host}] Raw Body: {text}")

            if "application/json" not in content_type:
                raise ValueError(f"{self.host} response was not JSON")

            result = resp.json()

            if result.get("status_code", 200) == 200 or result.get("success"):
                image_data = result["data"].get("image", result["data"])
                return {
                    "img_url": result["data"].get("medium", {}).get("url", image_data["url"]),
                    "raw_url": image_data["url"],
                    "web_url": result["data"].get("url_viewer", image_data["url"]),
                }
            else:
                raise ValueError(f"{self.host} error: {result.get('error', result)}")

        except Exception as e:
            log.error(f"[{self.host}] Failed to upload {image_path}: {repr(e)}")
            raise

    async def upload_batch(self, client, image_paths, title=None, adult=False):
        """Upload multiple images and return a list of results."""
        max_concurrent = 6  # Higher numbers lead to server side connection drops before we can read url
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
                        log.warning(f"[{self.host}] Invalid upload result: {result}")
                        results.append(None)
                except Exception as e:
                    log.error(f"[{self.host}] Upload failed: {e}")
                    results.append(None)
        return results
