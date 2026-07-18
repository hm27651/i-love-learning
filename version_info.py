from __future__ import annotations

import json
import os
from pathlib import Path


APP_VERSION = "2.0"


def load_version_info(root: Path) -> dict[str, str]:
    candidates = (
        root / "version.json",
        root / "packaging" / "windows" / "version.json",
    )
    raw: dict[str, object] = {}
    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        break
    return {
        "version": str(raw.get("version") or os.environ.get("STUDY_APP_VERSION") or APP_VERSION),
        "channel": str(raw.get("channel") or "development"),
        "build_commit": str(raw.get("build_commit") or os.environ.get("STUDY_BUILD_COMMIT") or "development"),
        "build_time": str(raw.get("build_time") or ""),
    }
