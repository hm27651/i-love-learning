from __future__ import annotations

import json
from datetime import date, timedelta

from flask import abort

from services.core.common_service import now_iso
from services.core.project_service import settings


OBJECTIVE_TYPES = {"single", "multiple", "true_false"}


def parse_options(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_answer(qtype, text):
    value = text.strip()
    if qtype == "multiple":
        return sorted({part.strip().upper() for part in value.replace("；", ",").split(",") if part.strip()})
    if qtype == "true_false":
        return ["true" if value.lower() in {"true", "1", "对", "正确"} else "false"]
    return [value.upper() if qtype == "single" else value]


def question_rows(conn, where="", params=(), limit=None, offset=0):
    sql = """
    SELECT q.*, kp.name knowledge_point_name, c.id chapter_id, c.name chapter_name,
           s.id subject_id, s.project_id, s.name subject_name, s.code exam_code,
           COALESCE(p.mastery_level,0) mastery_level, p.due_date,
           COALESCE(p.error_count,0) error_count, COALESCE(p.attempts,0) attempts
    FROM questions q
    JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
    JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
    LEFT JOIN question_progress p ON p.question_id=q.id
    """ + (" WHERE " + where if where else "") + " ORDER BY q.updated_at DESC"
    query_params = list(params)
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        query_params.extend([limit, offset])
    rows = conn.execute(sql, query_params).fetchall()
    return [dict(r) | {"options": json.loads(r["options_json"]), "answer": json.loads(r["answer_json"])} for r in rows]


def question_count(conn, where="", params=()):
    sql = """SELECT COUNT(*) n FROM questions q
      JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
      JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id"""
    if where:
        sql += " WHERE " + where
    return conn.execute(sql, params).fetchone()["n"]


def get_question(conn, question_id, project_id=None):
    where, params = ["q.id=?"], [question_id]
    if project_id is not None:
        where.append("s.project_id=?")
        params.append(project_id)
    rows = question_rows(conn, " AND ".join(where), params)
    if not rows:
        abort(404)
    return rows[0]


def interval_days(conn, new_level):
    values = [int(x) for x in settings(conn)["intervals"].split(",") if x.strip().isdigit()]
    values = (values + [30, 30, 30, 30])[:4]
    return values[max(0, min(new_level, 4)) - 1] if new_level > 0 else 1


def record_attempt(conn, question, mode, is_correct=None, rating=None, session_id=None):
    existing = conn.execute("SELECT * FROM question_progress WHERE question_id=?", (question["id"],)).fetchone()
    level = existing["mastery_level"] if existing else 0
    attempts = (existing["attempts"] if existing else 0) + 1
    correct_attempts = existing["correct_attempts"] if existing else 0
    errors = existing["error_count"] if existing else 0
    if rating == "fuzzy":
        level = max(0, level - 1)
        due = date.today() + timedelta(days=3)
    elif rating == "unknown" or is_correct is False:
        level = 0
        errors += 1
        due = date.today() + timedelta(days=1)
    else:
        level = min(4, level + 1)
        correct_attempts += 1
        due = date.today() + timedelta(days=interval_days(conn, level))
    conn.execute(
        """
      INSERT INTO question_progress(question_id,mastery_level,due_date,attempts,correct_attempts,error_count,last_attempt_at)
      VALUES (?,?,?,?,?,?,?)
      ON CONFLICT(question_id) DO UPDATE SET mastery_level=excluded.mastery_level,due_date=excluded.due_date,
      attempts=excluded.attempts,correct_attempts=excluded.correct_attempts,error_count=excluded.error_count,last_attempt_at=excluded.last_attempt_at
    """,
        (question["id"], level, due.isoformat(), attempts, correct_attempts, errors, now_iso()),
    )
    conn.execute(
        "INSERT INTO attempts(question_id,mode,is_correct,self_rating,answered_at,session_id) VALUES (?,?,?,?,?,?)",
        (question["id"], mode, None if is_correct is None else int(is_correct), rating, now_iso(), session_id),
    )
    return {"before": existing["mastery_level"] if existing else 0, "after": level, "due_date": due.isoformat()}


def grade_objective(question, form):
    if question["type"] == "multiple":
        submitted = sorted(form.getlist("answer"))
    else:
        submitted = [form.get("answer", "")]
    return submitted == question["answer"], submitted
