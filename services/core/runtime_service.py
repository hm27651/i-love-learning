from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import current_app


def _extension(name: str) -> Any:
    try:
        return current_app.extensions[name]
    except KeyError as exc:
        raise RuntimeError(f"application extension is not configured: {name}") from exc


def db():
    """Return the current application's database context manager."""
    return _extension("study_db")()


def data_dir() -> Path:
    return Path(current_app.config["STUDY_DATA_DIR"])


def db_path() -> Path:
    return Path(current_app.config["STUDY_DB_PATH"])


def backup_dir() -> Path:
    return Path(current_app.config["STUDY_BACKUP_DIR"])


def backup_data_snapshot(conn, label: str):
    return _extension("study_backup_snapshot")(conn, label)


def import_queue():
    return _extension("study_import_queue")


def export_queue():
    return _extension("study_export_queue")
