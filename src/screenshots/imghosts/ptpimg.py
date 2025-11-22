import aiofiles

from .base import BaseImageHost


class PTPIMG(BaseImageHost):
    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config["SCREENS"]["API_KEYS"].get("ptpimg")
        if not self.api_key:
            raise KeyError("API key 'ptpimg' not found in config['SCREENS']")
        self.referer = "https://ptpimg.me/index.php"
        self.upload_url = "https://ptpimg.me/upload.php"

    async def upload(self, client, image_path, title=None, adult=False):
        async with aiofiles.open(image_path, "rb") as f:
            file_data = await f.read()

        headers = {"referer": self.referer}
        data = {"format": "json", "api_key": self.api_key}
        files = {"file-upload[0]": (image_path, file_data, "image/png")}
        resp = await client.post(self.upload_url, headers=headers, data=data, files=files)
        self._validate_response(resp, "ptpimg")

        result = resp.json()
        if result and "code" in result[0]:
            ptpimg_code = result[0]["code"]
            ptpimg_ext = result[0]["ext"]
            img_url = f"https://ptpimg.me/{ptpimg_code}.{ptpimg_ext}"
            return {"img_url": img_url, "raw_url": img_url, "web_url": img_url}
        raise ValueError(f"ptpimg upload failed: {result.get('error', 'Unknown error')}")

    async def upload_batch(self, client, image_paths, title=None, adult=False):
        data = {"format": "json", "api_key": self.api_key}
        files = {}
        for i, image_path in enumerate(image_paths):
            async with aiofiles.open(image_path, "rb") as f:
                file_data = await f.read()
            files[f"file-upload[{i}]"] = (image_path, file_data, "image/png")

        headers = {"referer": self.referer}
        resp = await client.post(self.upload_url, headers=headers, data=data, files=files)
        self._validate_response(resp, "ptpimg")

        result = resp.json()
        if result and isinstance(result, list):
            batch_results = []
            for item in result:
                if "code" in item and "ext" in item:
                    ptpimg_code = item["code"]
                    ptpimg_ext = item["ext"]
                    img_url = f"https://ptpimg.me/{ptpimg_code}.{ptpimg_ext}"
                    batch_results.append({"img_url": img_url, "raw_url": img_url, "web_url": img_url})
                else:
                    batch_results.append(None)
            return batch_results
        raise ValueError(f"ptpimg batch upload failed: {result.get('error', 'Unknown error')}")
