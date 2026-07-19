from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from version_info import APP_VERSION


def _intervals(conn: sqlite3.Connection) -> list[int]:
    row = conn.execute("SELECT value FROM settings WHERE key='intervals'").fetchone()
    values = [int(item) for item in (row[0] if row else "3,7,14,30").split(",") if item.strip().isdigit()]
    return (values + [30, 30, 30, 30])[:4]


def _rebuild_progress(conn: sqlite3.Connection, question_ids: set[int]) -> None:
    intervals = _intervals(conn)
    for question_id in question_ids:
        attempts = conn.execute(
            "SELECT * FROM attempts WHERE question_id=? ORDER BY answered_at,id", (question_id,)
        ).fetchall()
        conn.execute("DELETE FROM question_progress WHERE question_id=?", (question_id,))
        if not attempts:
            continue
        level = correct = errors = 0
        due = None
        for attempt in attempts:
            answered = datetime.fromisoformat(attempt["answered_at"]).date()
            rating = attempt["self_rating"]
            if rating == "fuzzy":
                level = max(0, level - 1)
                due = answered + timedelta(days=3)
            elif rating == "unknown" or attempt["is_correct"] == 0:
                level = 0
                errors += 1
                due = answered + timedelta(days=1)
            else:
                level = min(4, level + 1)
                correct += 1
                due = answered + timedelta(days=intervals[level - 1])
        conn.execute(
            """INSERT INTO question_progress
               (question_id,mastery_level,due_date,attempts,correct_attempts,error_count,last_attempt_at)
               VALUES (?,?,?,?,?,?,?)""",
            (question_id, level, due.isoformat(), len(attempts), correct, errors, attempts[-1]["answered_at"]),
        )


def _session_rows(conn: sqlite3.Connection, project_id: int, from_date: str, to_date: str, mode: str):
    rows = []
    practice = conn.execute(
        """SELECT ps.*,COUNT(a.id) answered_count FROM practice_sessions ps
           LEFT JOIN attempts a ON a.session_id=ps.id
           WHERE ps.project_id=? GROUP BY ps.id ORDER BY ps.started_at DESC""",
        (project_id,),
    ).fetchall()
    for row in practice:
        if mode and row["mode"] != mode:
            continue
        rows.append({
            "key": f"practice:{row['id']}", "id": row["id"], "kind": row["mode"],
            "status": row["status"], "started_at": row["started_at"],
            "finished_at": row["completed_at"] or row["terminated_at"],
            "question_count": len(json.loads(row["question_ids_json"] or "[]")),
            "answered_count": row["answered_count"], "score": None,
        })
    if not mode or mode == "mock":
        mocks = conn.execute(
            """SELECT me.*,COUNT(a.id) answered_count FROM mock_exams me
               LEFT JOIN attempts a ON a.session_id=me.id
               WHERE me.project_id=? GROUP BY me.id ORDER BY me.started_at DESC""",
            (project_id,),
        ).fetchall()
        for row in mocks:
            rows.append({
                "key": f"mock:{row['id']}", "id": row["id"], "kind": "mock",
                "status": row["status"], "started_at": row["started_at"],
                "finished_at": row["submitted_at"] or row["terminated_at"],
                "question_count": len(json.loads(row["question_ids_json"] or "[]")),
                "answered_count": row["answered_count"], "score": row["score"],
            })
    if from_date:
        rows = [row for row in rows if row["started_at"][:10] >= from_date]
    if to_date:
        rows = [row for row in rows if row["started_at"][:10] <= to_date]
    return sorted(rows, key=lambda row: row["started_at"], reverse=True)[:200]


def _backup_rows(backup_dir: Path, db_name: str):
    if not backup_dir.exists():
        return []
    rows = []
    for folder in sorted((item for item in backup_dir.iterdir() if item.is_dir()), reverse=True)[:20]:
        database = folder / "data" / db_name
        rows.append({
            "name": folder.name,
            "path": str(folder),
            "created_at": datetime.fromtimestamp(folder.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
            "database_size": database.stat().st_size if database.exists() else 0,
        })
    return rows


def _runtime_mode(data_dir: Path, backup_dir: Path) -> str:
    if data_dir.as_posix() == "/data" or backup_dir.as_posix() == "/backups":
        return "Linux Docker"
    if (data_dir.parent / "I-Love-Learning.exe").exists() or (data_dir.parent / "_internal").exists():
        return "Windows Portable"
    if os.environ.get("STUDY_DATA_DIR"):
        return "源码调试（自定义数据目录）"
    if os.environ.get("H3CSE_DATA_DIR"):
        return "旧版兼容环境变量"
    return "源码调试（默认兼容 data 目录）"


def _data_locations(db_path: Path, backup_dir: Path) -> dict[str, str]:
    data_dir = db_path.parent
    return {
        "runtime_mode": _runtime_mode(data_dir, backup_dir),
        "data_dir": str(data_dir),
        "database": str(db_path),
        "uploads": str(data_dir / "uploads"),
        "imports": str(data_dir / "imports"),
        "exports": str(data_dir / "exports"),
        "backups": str(backup_dir),
    }


def create_data_management_blueprint(db_provider, current_project_fn, backup_fn, backup_dir_fn, db_path_fn):
    blueprint = Blueprint("data_management", __name__)

    @blueprint.get("/data-management")
    def index():
        from_date = request.args.get("from_date", "")
        to_date = request.args.get("to_date", "")
        mode = request.args.get("mode", "")
        with db_provider() as conn:
            project = current_project_fn(conn)
            rows = _session_rows(conn, project["id"], from_date, to_date, mode)
        backup_dir = Path(backup_dir_fn())
        return render_template(
            "data_management.html", project=project, rows=rows, from_date=from_date,
            to_date=to_date, mode=mode, backup_dir=str(backup_dir),
            data_locations=_data_locations(Path(db_path_fn()), backup_dir),
            backups=_backup_rows(backup_dir, Path(db_path_fn()).name),
        )

    @blueprint.post("/data-management/backup")
    def create_backup():
        with db_provider() as conn:
            target = backup_fn(conn, "manual")
        database = Path(target) / Path(db_path_fn()).name
        check = sqlite3.connect(f"file:///{database.resolve().as_posix()}?mode=ro", uri=True)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        flash(f"完整备份已创建，数据库检查：{integrity}。位置：{Path(target).parent}", "success")
        return redirect(url_for("data_management.index"))

    @blueprint.post("/data-management/cleanup")
    def cleanup():
        if request.form.get("confirmation") != "清理所选记录":
            flash("请输入“清理所选记录”确认操作", "error")
            return redirect(url_for("data_management.index"))
        selected = list(dict.fromkeys(request.form.getlist("session_key")))
        if not selected or len(selected) > 100:
            flash("请选择 1–100 个学习会话", "error")
            return redirect(url_for("data_management.index"))
        practice_ids = [item.split(":", 1)[1] for item in selected if item.startswith("practice:")]
        mock_ids = [item.split(":", 1)[1] for item in selected if item.startswith("mock:")]
        if len(practice_ids) + len(mock_ids) != len(selected):
            abort(400)
        with db_provider() as conn:
            project = current_project_fn(conn)
            found_practice = {
                row[0] for row in conn.execute(
                    f"SELECT id FROM practice_sessions WHERE project_id=? AND id IN ({','.join('?' for _ in practice_ids)})",
                    [project["id"], *practice_ids],
                )
            } if practice_ids else set()
            found_mock = {
                row[0] for row in conn.execute(
                    f"SELECT id FROM mock_exams WHERE project_id=? AND id IN ({','.join('?' for _ in mock_ids)})",
                    [project["id"], *mock_ids],
                )
            } if mock_ids else set()
            if found_practice != set(practice_ids) or found_mock != set(mock_ids):
                abort(404)
            target = backup_fn(conn, "learning_cleanup")
            ids = [*practice_ids, *mock_ids]
            marks = ",".join("?" for _ in ids)
            question_ids = {row[0] for row in conn.execute(
                f"SELECT DISTINCT question_id FROM attempts WHERE session_id IN ({marks})", ids
            )}
            deleted_attempts = conn.execute(f"DELETE FROM attempts WHERE session_id IN ({marks})", ids).rowcount
            if practice_ids:
                marks = ",".join("?" for _ in practice_ids)
                conn.execute(f"DELETE FROM practice_sessions WHERE id IN ({marks})", practice_ids)
            if mock_ids:
                marks = ",".join("?" for _ in mock_ids)
                conn.execute(f"DELETE FROM mock_exams WHERE id IN ({marks})", mock_ids)
            _rebuild_progress(conn, question_ids)
        flash(
            f"已清理 {len(selected)} 个学习会话和 {deleted_attempts} 条答题记录；操作前备份：{Path(target).parent}",
            "success",
        )
        return redirect(url_for("data_management.index"))

    @blueprint.get("/health")
    def health():
        try:
            with db_provider() as conn:
                integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
                schema = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            status = 200 if integrity == "ok" else 503
            return {
                "status": "ok" if status == 200 else "degraded",
                "database": integrity,
                "schema": schema,
                "version": os.environ.get("STUDY_APP_VERSION", APP_VERSION),
                "build_commit": os.environ.get("STUDY_BUILD_COMMIT", "development"),
            }, status
        except sqlite3.Error:
            return {"status": "unavailable", "database": "error"}, 503

    return blueprint
