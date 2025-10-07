from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

DEFAULT_IMAGE_SETTINGS: Dict[str, Any] = {
    "enable": True,
    "model": "gpt-image-1",
    "size": "1024x1024",
    "quality": "high",
    "background": "auto",
    "output_format": "png",
    "output_compression": 85,
    "mode": "hybrid",
    "font_path": "assets/fonts/DejaVuSans-Bold.ttf",
}

VALID_KEYS = set(DEFAULT_IMAGE_SETTINGS)


def load_image_settings() -> Dict[str, Any]:
    "Return image generation settings with optional JSON overrides."
    settings = dict(DEFAULT_IMAGE_SETTINGS)
    candidates = []
    env_path = os.getenv("ORGANISATION_IMAGE_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path("config/organisation_image.json"))
    candidates.append(Path("examples/organisation_image.json"))
    for candidate in candidates:
        if not candidate or not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key, value in data.items():
            if key in VALID_KEYS and value is not None:
                settings[key] = value
        break
    return settings
