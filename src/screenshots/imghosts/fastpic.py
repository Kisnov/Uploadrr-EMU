import aiofiles
import xml.etree.ElementTree as ET
from .base import BaseImageHost

class FASTPIC(BaseImageHost):
    def __init__(self, config):
        super().__init__(config)
        self.api_url = "https://fastpic.org/upload?api=1" 
        self.thumb_width = self.config['SCREENS'].get('size', '350')

    async def upload(self, client, image_path, title=None):
        async with aiofiles.open(image_path, 'rb') as f:
            data = await f.read()

        files = {
            'method': (None, 'file', 'text/plain'),
            'file1': (image_path, data, 'image/png'),
            'check_thumb': (None, 'no', 'text/plain'),
            'uploading': (None, '1', 'text/plain'),
            'orig_rotate': (None, '0', 'text/plain'),
            'thumb_size': (None, str(self.thumb_width), 'text/plain'),
        }

        resp = await client.post(self.api_url, files=files)
        self._validate_response(resp, "fastpic")

        root = ET.fromstring(resp.text)
        status = root.find("status")
        if status is not None and status.text == "ok":
            raw_url = root.find("imagepath").text
            img_url = root.find("thumbpath").text
            view_url = root.find("viewfullurl").text.replace("fastpic.org/view/", "fastpic.org/fullview/")
            return {
                'img_url': img_url, 
                'raw_url': raw_url,
                'web_url': view_url
            }
        
        error = root.find("error")
        if error is not None:
            log.error(f"Fastpic upload failed: {error.text}")  # noqa: F821
        raise ValueError("Fastpic upload failed: Invalid response or error encountered")