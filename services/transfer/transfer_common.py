from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from app_runtime import connect_database


PACKAGE_FORMAT = "i-love-learning-question-bank"
PACKAGE_VERSION = 1
MANIFEST_COMMENT_PREFIX = b"ILL-MANIFEST-SHA256:"
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_PACKAGE_FILES = 5000
MAX_COMPRESSION_RATIO = 100
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
QUESTION_TYPES = {"single", "multiple", "true_false", "fill", "short"}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _connect(db_path: Path) -> sqlite3.Connection:
    return connect_database(db_path)


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _safe_filename(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "-", value).strip(" .-")
    return value[:80] or "题库"

