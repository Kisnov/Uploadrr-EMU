import io
import math
import subprocess

import numpy as np
from PIL import Image

from src.utils.console import log


class PerceptualHash:
    """
    A class to match Stash's pHash implementation, as goimagehash diverges from imagehash.
    REF: https://github.com/stashapp/stash/blob/develop/pkg/hash/videophash/phash.go
         https://github.com/corona10/goimagehash/blob/v1.1.0
    Creates perfect matches to StashDB's pHash.
    """

    def compute_phash(self, filepath: str, duration: float) -> str:
        """
        Compute pHash for a video file by generating a 5x5 sprite and hashing it.
        Args:
            filepath: Path to the video file.
            duration: Video duration in seconds.
        Returns:
            64-bit pHash as a 16-character hexadecimal string, or empty string on error.
        """
        try:
            width = 160
            columns = rows = 5
            chunk_count = columns * rows
            offset = 0.05 * duration
            step_size = (0.9 * duration) / chunk_count

            images = []
            for i in range(chunk_count):
                seek_time = offset + (i * step_size)
                cmd = [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    str(seek_time),
                    "-i",
                    str(filepath),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale={width}:-2",
                    "-c:v",
                    "bmp",
                    "-f",
                    "rawvideo",
                    "pipe:",
                ]
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
                img = Image.open(io.BytesIO(result.stdout)).convert("RGB")
                images.append(img)

            # Get dimensions from first image
            sprite_width = images[0].size[0]
            sprite_height = images[0].size[1]

            # Build sprite
            sprite = Image.new("RGB", (sprite_width * columns, sprite_height * rows))
            for idx, img in enumerate(images):
                x = (idx % columns) * sprite_width
                y = (idx // rows) * sprite_height
                sprite.paste(img, (x, y))

            phash = self._phash(sprite)
            log.debug(f"PHASH = {phash}")
            return phash

        except Exception as e:
            log.error(f"PHASH failed: {e}")
            return ""

    def _forward_transform(self, arr: np.ndarray) -> np.ndarray:
        """
        Generic forwardTransformStaticN (See: https://github.com/corona10/goimagehash/blob/v1.1.0/transforms/static.go)
        N must be a power of two (4, 8, 16, 32).
        """
        n = len(arr)
        if n == 2:
            return np.array([arr[0] + arr[1], (arr[0] - arr[1]) / (2 * math.cos(0.5 * math.pi / 2))])

        half = n // 2
        dctn = [2 * math.cos((i + 0.5) * math.pi / n) for i in range(half)]
        temp = np.zeros(n)

        # Symmetric folding
        for i in range(half):
            x, y = arr[i], arr[n - 1 - i]
            temp[i] = x + y
            temp[i + half] = (x - y) / dctn[i]

        # Recurse on halves
        temp[:half] = self._forward_transform(temp[:half])
        temp[half:] = self._forward_transform(temp[half:])

        # Stitch outputs
        out = np.zeros(n)
        for i in range(half - 1):
            out[i * 2] = temp[i]
            out[i * 2 + 1] = temp[i + half] + temp[i + half + 1]
        out[n - 2] = temp[half - 1]
        out[n - 1] = temp[-1]

        return out

    def _dct1d_fast64(self, arr: np.ndarray) -> np.ndarray:
        return self._forward_transform(arr)

    def _dct2d_fast64(self, pixels: np.ndarray) -> np.ndarray:
        if pixels.shape != (64, 64):
            raise ValueError("Input must be 64x64")

        # DCT rows
        dct1 = np.array([self._dct1d_fast64(row) for row in pixels])
        # DCT cols
        dct2 = np.array([self._dct1d_fast64(dct1[:, i]) for i in range(64)]).T
        return dct2

    def _phash(self, image: Image.Image) -> str:
        """
        Replicates PerceptionHash from: https://github.com/corona10/goimagehash/blob/v1.1.0/hashcompute.go
        - Resize to 64x64 with BILINEAR
        - Grayscale (weighted luminance with scaling: R/257, G/257, B/256)
        - 64x64 DCT (DCT1DFast64 algorithm)
        - Take top-left 8x8 (including [0,0])
        - Median threshold
        - Bit order: (0,0) = MSB
        """
        resized = image.resize((64, 64), Image.BILINEAR)
        rgb_array = np.array(resized, dtype=np.float64)
        pixels = 0.299 * (rgb_array[:, :, 0] / 257) + 0.587 * (rgb_array[:, :, 1] / 257) + 0.114 * (rgb_array[:, :, 2] / 256)
        dct2 = self._dct2d_fast64(pixels)
        coeffs = dct2[0:8, 0:8]  # shape (8,8)
        flat = coeffs.flatten()
        median_val = np.median(flat)
        hash_int = 0
        for val in flat:
            hash_int = (hash_int << 1) | (1 if val > median_val else 0)
        return f"{hash_int:016x}"
