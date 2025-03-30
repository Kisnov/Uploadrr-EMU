import pyimgbox
from .base import BaseImageHost

class IMGBOX(BaseImageHost):
    async def upload(self, client, image_path):
        uploaded_images = []
        async with pyimgbox.Gallery(thumb_width=350, square_thumbs=False) as gallery:
            async for submission in gallery.add([image_path]):
                if not submission['success']:
                    log.debug(f"Imgbox failed for {submission['filename']}: {submission['error']}")  # noqa: F821
                    continue
                uploaded_images.append({
                    'img_url': submission['thumbnail_url'],
                    'raw_url': submission['image_url'],
                    'web_url': submission['web_url'],
                    'local_file': submission['filename']
                })
        if uploaded_images:
            return uploaded_images[0]
        raise ValueError("Imgbox upload failed")