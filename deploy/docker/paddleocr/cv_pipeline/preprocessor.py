"""Step 0: Input normalization.

Pillow-based image preprocessing -- RGB conversion, resize.
"""

from __future__ import annotations

import io
import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

MAX_DIMENSION = 2048


class ImagePreprocessor:
    """Image input normalization."""

    def normalize(self, image_bytes: bytes) -> tuple[np.ndarray, Image.Image]:
        """Image -> RGB numpy + PIL Image.

        - RGBA/Palette -> RGB conversion
        - Aspect-preserving resize if >2048px

        Args:
            image_bytes: Raw image bytes

        Returns:
            (numpy BGR array for OpenCV, PIL RGB Image)
        """
        img = Image.open(io.BytesIO(image_bytes))

        if img.mode != "RGB":
            logger.debug("Converting image from %s to RGB", img.mode)
            img = img.convert("RGB")

        # Aspect-preserving resize
        w, h = img.size
        if max(w, h) > MAX_DIMENSION:
            scale = MAX_DIMENSION / max(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            logger.debug("Resized image from %dx%d to %dx%d", w, h, new_w, new_h)

        # PIL RGB -> numpy RGB -> OpenCV BGR
        rgb_array = np.array(img)
        bgr_array = rgb_array[:, :, ::-1].copy()

        return bgr_array, img
