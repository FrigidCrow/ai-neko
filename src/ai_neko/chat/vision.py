"""Validate explicitly supplied single-turn images without persisting their bytes."""

from __future__ import annotations

import base64
import binascii
import math
import time

MAX_IMAGE_BYTES = 3 * 1024 * 1024


def validate_image(value: dict | None) -> dict | None:
    if value is None:
        return None
    required = {"frame_id", "source_id", "source_name", "captured_at", "data_url"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("invalid_image")
    for key, limit in (("frame_id", 128), ("source_id", 256), ("source_name", 512)):
        item = value[key]
        if not isinstance(item, str) or not item.strip() or len(item) > limit:
            raise ValueError("invalid_image_source")
        if any(ord(c) < 32 for c in item):
            raise ValueError("invalid_image_source")
    captured = value["captured_at"]
    # Desktop Date.now() is milliseconds; the wire contract also accepts seconds.
    if isinstance(captured, (int, float)) and not isinstance(captured, bool):
        captured = captured / 1000 if captured > 100_000_000_000 else captured
    else:
        raise ValueError("invalid_image_time")
    if not math.isfinite(captured) or not -5 <= time.time() - captured <= 120:
        raise ValueError("stale_image")
    url = value["data_url"]
    if not isinstance(url, str) or len(url) > MAX_IMAGE_BYTES * 4 // 3 + 100:
        raise ValueError("image_too_large")
    prefix, separator, encoded = url.partition(",")
    if not separator or prefix not in {"data:image/jpeg;base64", "data:image/png;base64"}:
        raise ValueError("invalid_image_type")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("invalid_image_data") from None
    signature = b"\xff\xd8\xff" if "jpeg" in prefix else b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature) or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("invalid_image_data")
    return {**value, "captured_at": captured}


def attach_image(messages: list[dict], image: dict | None) -> list[dict]:
    """Image bytes exist only in the live request, never the graph/checkpoint state."""
    copied = [dict(message) for message in messages]
    if image is not None:
        for message in reversed(copied):
            if message.get("role") == "user":
                text = message["content"]
                message["content"] = [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": image["data_url"]}},
                ]
                break
    return copied
