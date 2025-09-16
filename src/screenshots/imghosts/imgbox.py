import pyimgbox

from src.utils.console import log

from .base import BaseImageHost


class IMGBOX(BaseImageHost):
    def __init__(self, config):
        super().__init__(config)

    async def upload(self, client, image_path, title=None, adult=False):
        # Shouldn't be used per image; redirect to batch
        raise NotImplementedError("Use upload_batch instead.")

    async def upload_batch(self, client, image_paths, title=None, adult=False):
        log.debug(f"[imgbox] Preparing to upload {len(image_paths)} images: {image_paths}")
        uploaded_images = []
        try:
            async with pyimgbox.Gallery(title=title or "Untitled", thumb_width=350, square_thumbs=False, adult=adult) as gallery:
                async for submission in gallery.add(image_paths):
                    if not submission["success"]:
                        log.debug(f"[imgbox] Failed to upload '{submission['filename']}': {submission.get('error', 'Unknown error')}")
                        continue
                    uploaded_images.append({
                        "img_url": submission["thumbnail_url"],
                        "raw_url": submission["image_url"],
                        "web_url": submission["web_url"],
                        "local_file": submission["filename"],
                    })
        except Exception as e:
            log.error(f"[imgbox] Upload batch failed: {e!r}")
            raise
        if uploaded_images:
            return uploaded_images
        raise ValueError("Imgbox upload failed")
