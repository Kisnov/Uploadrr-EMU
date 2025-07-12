from abc import ABC, abstractmethod

from src.utils.console import log  # noqa: F401


class BaseImageHost(ABC):
    def __init__(self, config):
        self.config = config

    @abstractmethod
    async def upload(self, client, image_path):
        """Upload an image and return a dict with img_url, raw_url, and web_url."""
        pass

    def _validate_response(self, response, host_name):
        """Optional helper for response validation."""
        if response.status_code != 200:
            raise ValueError(f"{host_name} upload failed: {response.status_code} - {response.text[:200]}...")
