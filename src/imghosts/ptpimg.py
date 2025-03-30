import aiofiles
from .base import BaseImageHost

class PTPIMG(BaseImageHost):
    def __init__(self, config):
        super().__init__(config)
        self.api_key = self.config['SCREENS'].get('ptpimg_api')
        if not self.api_key:
            raise KeyError("API key 'ptpimg_api' not found in config['SCREENS']")

    async def upload(self, client, image_path):
        async with aiofiles.open(image_path, 'rb') as f:
            data = await f.read()

        headers = {'referer': 'https://ptpimg.me/index.php'}
        files = {
            'api_key': (None, self.api_key, 'text/plain'),
            'file-upload[0]': (image_path, data, 'image/png')
        }
        resp = await client.post("https://ptpimg.me/upload.php", headers=headers, files=files)
        self._validate_response(resp, "ptpimg")
        
        result = resp.json()
        if result and 'code' in result[0]:
            ptpimg_code = result[0]['code']
            ptpimg_ext = result[0]['ext']
            img_url = f"https://ptpimg.me/{ptpimg_code}.{ptpimg_ext}"
            return {
                'img_url': img_url,
                'raw_url': img_url,
                'web_url': img_url
            }
        raise ValueError(f"ptpimg upload failed: {result.get('error', 'Unknown error')}")