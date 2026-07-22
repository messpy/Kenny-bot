from __future__ import annotations

from src.kennybot.ai.openai_vision import detect_image_mime_type


def test_detect_image_mime_type() -> None:
    assert detect_image_mime_type(b"\x89PNG\r\n\x1a\nx") == "image/png"
    assert detect_image_mime_type(b"\xff\xd8\xffx") == "image/jpeg"
    assert detect_image_mime_type(b"GIF89ax") == "image/gif"
    assert detect_image_mime_type(b"RIFFxxxxWEBPx") == "image/webp"
