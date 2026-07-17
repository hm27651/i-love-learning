from __future__ import annotations

import json
from datetime import datetime


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def parse_iso(value):
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def json_object(value, fallback=None):
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else (fallback if fallback is not None else {})
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback if fallback is not None else {}
