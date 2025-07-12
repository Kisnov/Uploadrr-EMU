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
        self.api_key = self.config["SCREENS"].get(api_key_name)
        if not self.api_key:
            raise KeyError(f"API key '{api_key_name}' not found in config['SCREENS']")

    async def upload(self, client, image_path, title):
        try:
            async with aiofiles.open(image_path, "rb") as f:
                img = await f.read()

            headers = {"X-API-Key": self.api_key}
            data = {"image": base64.b64encode(img).decode("utf8"), "tags": title}

            log.debug(f"[{self.host_name}] Uploading {image_path} with title '{title}'")
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
