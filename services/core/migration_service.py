from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from migrations import LATEST_SCHEMA_VERSION, migrate_database
from services.core.storage_service import backup_data_snapshot


class MigrationSafetyError(RuntimeError):
    pass


def schema_version(conn: sqlite3.Connection) -> int:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    }
    if not tables or tables <= {"schema_migrations"}:
        return 0
    if "schema_migrations" in tables:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        if row and row[0] is not None:
            return int(row[0])
    return 1


def migrate_with_snapshot(
    conn: sqlite3.Connection,
    *,
    data_dir: Path,
    db_path: Path,
    backup_dir: Path,
    logger: logging.Logger | None = None,
) -> dict[str, object]:
    """Upgrade the database with a pre-upgrade snapshot and post-upgrade checks."""
    before = schema_version(conn)
    snapshot: Path | None = None
    if 0 < before < LATEST_SCHEMA_VERSION:
        snapshot = backup_data_snapshot(
            conn,
            f"pre_schema_v{before}_to_v{LATEST_SCHEMA_VERSION}",
            data_dir=data_dir,
            db_path=db_path,
            backup_dir=backup_dir,
        )
        if logger:
            logger.info("database pre-migration snapshot created: %s", snapshot)

    try:
        after = migrate_database(conn)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok":
            raise MigrationSafetyError(f"database integrity check failed: {integrity}")
        if foreign_key_errors:
            raise MigrationSafetyError(f"database foreign key check failed: {len(foreign_key_errors)} errors")
    except Exception as exc:
        location = f"; pre-upgrade snapshot: {snapshot}" if snapshot else ""
        raise MigrationSafetyError(f"database migration was rejected{location}: {exc}") from exc

    if logger:
        logger.info(
            "database schema ready before=%s after=%s integrity=%s foreign_key_errors=0",
            before,
            after,
            integrity,
        )
    return {
        "before": before,
        "after": after,
        "snapshot": snapshot,
        "integrity": integrity,
        "foreign_key_errors": 0,
    }
