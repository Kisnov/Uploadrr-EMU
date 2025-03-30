import aiofiles
from .base import BaseImageHost

class PIXHOST(BaseImageHost):
    async def upload(self, client, image_path):
        async with aiofiles.open(image_path, 'rb') as f:
            data = await f.read()

        files = {
            'content_type': (None, '0', 'text/plain'),
            'max_th_size': (None, '350', 'text/plain'),
            'img': (image_path, data, 'image/png')
        }
        resp = await client.post("https://api.pixhost.to/images", files=files)
        self._validate_response(resp, "pixhost")
        
        result = resp.json()
        if 'th_url' in result:
            return {
                'img_url': result['th_url'],
                'raw_url': result['th_url'].replace('https://t', 'https://img').replace('/thumbs/', '/images/'),
                'web_url': result['show_url']
            }
        raise ValueError(f"pixhost upload failed: {result.get('error', 'Unknown error')}")