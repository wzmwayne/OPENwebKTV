import json
import os
from .config import settings

DEFAULTS = {
    "normal_size": 20,
    "focus_size": 26,
    "animation": True,
    "focus_mode": "size",
}


def load_settings() -> dict:
    try:
        with open(settings.LYRIC_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
    except Exception:
        return dict(DEFAULTS)


def save_settings(data: dict) -> dict:
    valid = {k: v for k, v in data.items() if k in DEFAULTS}
    os.makedirs(os.path.dirname(settings.LYRIC_SETTINGS_PATH), exist_ok=True)
    with open(settings.LYRIC_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump({**DEFAULTS, **valid}, f, ensure_ascii=False, indent=2)
    return {**DEFAULTS, **valid}
