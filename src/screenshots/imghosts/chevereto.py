import asyncio
import base64

import aiofiles

from src.utils.console import log

from .base import BaseImageHost


class CheveretoTemplate(BaseImageHost):
    def __init__(self, config, api_key_name, api_url, host_name):
        super().__init__(config)
        self.api_key_name = api_key_name
        self.api_url = api_url
        self.host_name = host_name

    async def upload(self, client, image_path, title=None, adult=False):
        """Upload an image to Chevereto and return a dict with img_url, raw_url, and web_url."""
        config_section = (
            self.config["ADULT"]
            if adult
            and (self.config.get("ADULT", {}).get(self.api_key_name) or self.config.get("ADULT", {}).get("imgbox_only", False))
            else self.config["SCREENS"]
        )
        api_key = config_section.get(self.api_key_name)

        if not api_key:
            if not adult or self.host_name == "imgbox":
                log.warning(
                    f"API key '{self.api_key_name}' not found in config['{'ADULT' if adult else 'SCREENS'}'] for {self.host_name}"
                )
            else:
                raise KeyError(f"API key '{self.api_key_name}' not found in config['ADULT'] for {self.host_name}")

        log.debug(f"Using API key for {self.host_name} (adult={adult}): {api_key}")
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

            log.debug(f"[{self.host_name}] HTTP {resp.status_code} {resp.reason_phrase}")
            log.debug(f"[{self.host_name}] Content-Type: {content_type}")
            log.debug(f"[{self.host_name}] Raw Body: {text[:500]}...")

            if "application/json" not in content_type:
                raise ValueError(f"{self.host_name} response was not JSON")

            result = resp.json()

            if result.get("status_code", 200) == 200 or result.get("success"):
                image_data = result["data"].get("image", result["data"])
                return {
                    "img_url": result["data"].get("medium", image_data)["url"],
                    "raw_url": image_data["url"],
                    "web_url": result["data"].get("url_viewer", image_data["url"]),
                }

            raise ValueError(f"{self.host_name} error: {result.get('error', result)}")

        except Exception as e:
            log.error(f"[{self.host_name}] Failed to upload {image_path}: {repr(e)}")
            raise

    async def upload_batch(self, client, image_paths, title=None, adult=False):
        """Upload multiple images and return a list of results."""
        tasks = [self.upload(client, path, title, adult) for path in image_paths]
        results = []
        for f in asyncio.as_completed(tasks):
            try:
                result = await f
                results.append(result)
            except Exception as e:
                log.debug(f"Upload failed for {self.host_name}: {e}")
                results.append(None)
        return results
