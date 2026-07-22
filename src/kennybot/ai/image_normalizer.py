from __future__ import annotations

import io

from PIL import Image, ImageOps, UnidentifiedImageError


class ImageNormalizeError(RuntimeError):
    """Raised when an image cannot be normalized."""


def normalize_image_for_vision(image_bytes: bytes) -> tuple[bytes, str]:
    """Return image bytes in a broadly supported format plus its MIME type."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            if getattr(image, "is_animated", False):
                image.seek(0)
                image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            output = io.BytesIO()
            if image.mode == "RGBA":
                image.save(output, format="PNG", optimize=True)
                return output.getvalue(), "image/png"
            image.save(output, format="JPEG", quality=90, optimize=True)
            return output.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageNormalizeError("Unsupported or invalid image file.") from exc
