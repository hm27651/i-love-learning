from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from flask import current_app, has_app_context
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parents[2]


def resolve_data_dir() -> Path:
    return Path(os.environ.get("STUDY_DATA_DIR") or os.environ.get("H3CSE_DATA_DIR", BASE_DIR / "data"))


def resolve_db_path(data_root: Path | None = None) -> Path:
    return (data_root or resolve_data_dir()) / os.environ.get("STUDY_DB_NAME", "h3cse.db")


def resolve_backup_dir() -> Path:
    return Path(os.environ.get("STUDY_BACKUP_DIR") or (Path.home() / "Documents" / "I-Love-Learning-Backup"))


DATA_DIR = resolve_data_dir()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = resolve_db_path(DATA_DIR)
BACKUP_DIR = resolve_backup_dir()
APP_PORT = int(os.environ.get("PORT", "23456"))
ALLOWED_IMAGES = {"png", "jpg", "jpeg", "gif", "webp"}


def active_data_dir() -> Path:
    if has_app_context() and current_app.config.get("STUDY_DATA_DIR"):
        return Path(current_app.config["STUDY_DATA_DIR"])
    return resolve_data_dir()


def ensure_data_dirs(data_root: Path | None = None) -> Path:
    root = data_root or active_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    uploads = root / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    return uploads


def backup_data_snapshot(
    conn: sqlite3.Connection,
    label: str,
    *,
    data_dir: Path | None = None,
    db_path: Path | None = None,
    backup_dir: Path | None = None,
) -> Path:
    data_dir = data_dir or DATA_DIR
    db_path = db_path or DB_PATH
    backup_dir = backup_dir or BACKUP_DIR
    target = backup_dir / f"{label}_{datetime.now():%Y%m%d_%H%M%S}" / "data"
    target.mkdir(parents=True, exist_ok=True)
    backup_db = sqlite3.connect(target / db_path.name)
    try:
        conn.backup(backup_db)
    finally:
        backup_db.close()
    for child in data_dir.iterdir():
        if child.resolve() == db_path.resolve() or child.name in {"h3cse.db-wal", "h3cse.db-shm"}:
            continue
        backup_root = backup_dir.resolve()
        if backup_root == child.resolve() or backup_root.is_relative_to(child.resolve()):
            continue
        destination = target / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(child, destination)
    return target


def save_image(file, *, upload_dir: Path | None = None) -> str | None:
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_IMAGES:
        raise ValueError("仅支持 PNG、JPG、GIF、WEBP 图片")
    uploads = upload_dir or ensure_data_dirs()
    uploads.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    file.save(uploads / name)
    return name


def lan_url() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
    except OSError:
        ip = "本机局域网IP"
    return f"http://{ip}:{APP_PORT}"
