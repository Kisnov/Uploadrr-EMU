import aiofiles
import base64
from .base import BaseImageHost

class CheveretoTemplate(BaseImageHost):
    def __init__(self, config, api_key_name, api_url, host_name):
        super().__init__(config)
        self.api_key_name = api_key_name
        self.api_url = api_url
        self.host_name = host_name
        self.api_key = self.config['SCREENS'].get(api_key_name)
        if not self.api_key:
            raise KeyError(f"API key '{api_key_name}' not found in config['SCREENS']")

    async def upload(self, client, image_path):
        try:
            async with aiofiles.open(image_path, 'rb') as f:
                data = await f.read()
                
            headers = {'X-API-Key': self.api_key}
            files = {
                'image': (None, base64.b64encode(data).decode('utf8'), 'text/plain')
            }
            resp = await client.post(self.api_url,headers=headers, files=files)
            self._validate_response(resp, self.host_name)
            
            result = resp.json()
            if result.get('status_code', 200) == 200 or result.get('success'):
                image_data = result['data']['image'] if 'image' in result['data'] else result['data']
                return {
                    'img_url': result['data'].get('medium', image_data)['url'],
                    'raw_url': image_data['url'],
                    'web_url': result['data'].get('url_viewer', image_data['url'])
                }
            raise ValueError(f"{self.host_name} upload failed: {result.get('error', 'Unknown error')}")
        except Exception as e:
            log.error(f"Failed to upload {image_path} to {self.host_name}: {str(e)}")  # noqa: F821
            raise