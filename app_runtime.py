from __future__ import annotations

import logging
import os
import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path


def load_or_create_secret(data_dir: Path) -> str:
    configured = os.environ.get("STUDY_SECRET") or os.environ.get("H3CSE_SECRET")
    if configured:
        return configured
    data_dir.mkdir(parents=True, exist_ok=True)
    secret_file = data_dir / ".secret_key"
    if secret_file.exists():
        value = secret_file.read_text(encoding="ascii").strip()
        if value:
            secret_file.chmod(0o600)
            return value
    value = secrets.token_hex(32)
    secret_file.write_text(value, encoding="ascii")
    secret_file.chmod(0o600)
    return value


def configure_file_logging(logger: logging.Logger, data_dir: Path) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target = (log_dir / "app.log").resolve()
    if any(isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == target for handler in logger.handlers):
        return
    handler = RotatingFileHandler(
        target, maxBytes=1024 * 1024, backupCount=5, encoding="utf-8", delay=True
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def close_file_logging(logger: logging.Logger, data_dir: Path) -> None:
    target = (data_dir / "logs" / "app.log").resolve()
    for handler in list(logger.handlers):
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == target:
            logger.removeHandler(handler)
            handler.close()


def configure_sqlite_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def connect_database(path: Path) -> sqlite3.Connection:
    return configure_sqlite_connection(sqlite3.connect(path, timeout=30))


def create_background_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix="study-background")
