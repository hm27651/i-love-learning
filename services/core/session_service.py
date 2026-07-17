from __future__ import annotations

import json
import uuid

from flask import render_template, request

from services.core.common_service import now_iso

ACTIVE_SESSION_STATUSES = ("active", "paused")


def active_practice_session(conn, project_id, mode):
    return conn.execute(
        """SELECT * FROM practice_sessions WHERE project_id=? AND mode=?
           AND status IN ('active','paused') ORDER BY started_at DESC LIMIT 1""",
        (project_id, mode),
    ).fetchone()


def active_mock_session(conn, project_id):
    return conn.execute(
        """SELECT * FROM mock_exams WHERE project_id=? AND status IN ('active','paused')
           ORDER BY started_at DESC LIMIT 1""",
        (project_id,),
    ).fetchone()


def session_progress(row):
    if not row:
        return None
    total = len(json.loads(row["question_ids_json"] or "[]"))
    return {"row": row, "total": total, "done": min(int(row["current_index"]), total)}


def issue_control_token(conn, table, item_id):
    token = uuid.uuid4().hex
    conn.execute(f"UPDATE {table} SET control_token=?,updated_at=? WHERE id=?", (token, now_iso(), item_id))
    return token


def control_token_matches(row, token):
    return bool(token and row["control_token"] and token == row["control_token"])


def session_conflict_response():
    message = "该会话已在另一台设备或另一个页面接管，请返回模块页后重新打开。"
    if request.is_json or request.accept_mimetypes.best == "application/json":
        return {"error": message, "code": "session_taken_over"}, 409
    return render_template("session_conflict.html", message=message), 409
