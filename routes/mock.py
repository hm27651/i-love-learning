from __future__ import annotations

import json
import random
import sqlite3
import uuid
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app import db
from services.core.common_service import json_object, now_iso, parse_iso
from services.core.project_service import current_project, module_disabled, module_enabled, project_settings
from services.questions.question_service import get_question, question_rows, record_attempt
from services.core.session_service import (
    ACTIVE_SESSION_STATUSES,
    active_mock_session,
    control_token_matches,
    issue_control_token,
    session_conflict_response,
)


bp = Blueprint("mock", __name__)


def mock_remaining_seconds(exam, at=None):
    remaining = max(0, int(exam["remaining_seconds"] or 0))
    if exam["status"] != "active":
        return remaining
    started = parse_iso(exam["active_started_at"])
    if not started:
        return remaining
    elapsed = max(0, int(((at or datetime.now()) - started).total_seconds()))
    return max(0, remaining - elapsed)


def normalize_mock_answer(question, values):
    if not isinstance(values, list):
        values = [values]
    clean = [str(value) for value in values if str(value)]
    return sorted(set(clean)) if question["type"] == "multiple" else clean[:1]


def merge_mock_form_answers(questions_list, saved, form):
    result = dict(saved)
    for question in questions_list:
        key = f"q_{question['id']}"
        if key in form:
            result[str(question["id"])] = normalize_mock_answer(question, form.getlist(key))
    return result


def finalize_mock_exam(conn, exam, project_id, answers):
    ids = json.loads(exam["question_ids_json"] or "[]")
    questions_list = [get_question(conn, qid, project_id) for qid in ids]
    normalized, correct = {}, 0
    for question in questions_list:
        submitted = normalize_mock_answer(question, answers.get(str(question["id"]), []))
        normalized[str(question["id"])] = submitted
        correct += int(submitted == question["answer"])
    score = round(100 * correct / len(questions_list), 1) if questions_list else 0
    now = now_iso()
    changed = conn.execute(
        """UPDATE mock_exams SET status='submitted',submitted_at=?,answers_json=?,score=?,
           remaining_seconds=0,active_started_at=NULL,control_token='',updated_at=?
           WHERE id=? AND status='active'""",
        (now, json.dumps(normalized, ensure_ascii=False), score, now, exam["id"]),
    ).rowcount
    if not changed:
        return False
    for question in questions_list:
        record_attempt(
            conn,
            question,
            "mock",
            is_correct=normalized[str(question["id"])] == question["answer"],
            session_id=exam["id"],
        )
    return True


@bp.route("/mock", methods=["GET", "POST"])
def setup():
    with db() as conn:
        project = current_project(conn)
        project_id = project["id"]
        if not module_enabled(conn, project_id, "mock"):
            return module_disabled(project, "模拟考试")
        cfg = project_settings(conn, project_id)
        active = active_mock_session(conn, project_id)
        if request.method == "POST":
            if active:
                flash("已有进行中或暂停的模拟考试，请先继续或终止旧会话", "error")
                return redirect(url_for("mock.setup"))
            count = max(1, min(200, int(request.form.get("count", cfg["qualifying_count"]))))
            minutes = max(1, min(300, int(request.form.get("minutes", cfg["qualifying_minutes"]))))
            chapter_ids = [int(x) for x in request.form.getlist("chapter_id")]
            where, params = [
                "q.status='verified'",
                "q.type IN ('single','multiple','true_false')",
                "s.project_id=?",
            ], [project_id]
            if chapter_ids:
                where.append("c.id IN (%s)" % ",".join("?" for _ in chapter_ids))
                params.extend(chapter_ids)
            rows = question_rows(conn, " AND ".join(where), params)
            random.shuffle(rows)
            chosen = rows[:count]
            if len(chosen) < count:
                flash(f"符合条件的已核验客观题只有 {len(chosen)} 道，无法生成 {count} 道试卷", "error")
                return redirect(url_for("mock.setup"))
            core_ids = {
                row["id"]
                for row in conn.execute(
                    """SELECT c.id FROM chapters c JOIN subjects s ON s.id=c.subject_id
                       WHERE s.project_id=? AND c.is_core=1""",
                    (project_id,),
                )
            }
            qualifying = (
                count >= int(cfg["qualifying_count"])
                and minutes == int(cfg["qualifying_minutes"])
                and core_ids.issubset(set(chapter_ids) if chapter_ids else core_ids)
            )
            exam_id = uuid.uuid4().hex
            now = now_iso()
            try:
                conn.execute(
                    """INSERT INTO mock_exams(id,project_id,started_at,question_ids_json,objective_count,
                       time_limit,qualifying,chapter_ids_json,status,remaining_seconds,active_started_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,'active',?,?,?)""",
                    (
                        exam_id,
                        project_id,
                        now,
                        json.dumps([r["id"] for r in chosen]),
                        count,
                        minutes,
                        int(qualifying),
                        json.dumps(chapter_ids),
                        minutes * 60,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("已有进行中或暂停的模拟考试", "error")
                return redirect(url_for("mock.setup"))
            return redirect(url_for("mock.exam", exam_id=exam_id))
        chapters = conn.execute(
            """SELECT c.*,s.name subject_name,
                 (SELECT COUNT(*) FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
                  WHERE kp.chapter_id=c.id AND q.status='verified'
                    AND q.type IN ('single','multiple','true_false')) question_count
               FROM chapters c JOIN subjects s ON s.id=c.subject_id
               WHERE s.project_id=? ORDER BY s.id,c.id""",
            (project_id,),
        ).fetchall()
        history = conn.execute(
            """SELECT * FROM mock_exams WHERE project_id=? AND submitted_at IS NOT NULL
               ORDER BY submitted_at DESC LIMIT 10""",
            (project_id,),
        ).fetchall()
        return render_template("mock_setup.html", chapters=chapters, history=history, cfg=cfg, active_exam=active)


@bp.route("/mock/<exam_id>", methods=["GET", "POST"])
def exam(exam_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        exam_row = conn.execute(
            "SELECT * FROM mock_exams WHERE id=? AND project_id=?", (exam_id, project_id)
        ).fetchone()
        if not exam_row:
            abort(404)
        if exam_row["status"] == "terminated":
            return render_template(
                "session_terminated.html",
                kind="mock",
                mode="mock",
                answered_count=0,
                total=exam_row["objective_count"],
            )
        ids = json.loads(exam_row["question_ids_json"])
        questions_list = [get_question(conn, qid, project_id) for qid in ids]
        if exam_row["status"] == "submitted" or exam_row["submitted_at"]:
            answers = json.loads(exam_row["answers_json"])
            details = []
            for question in questions_list:
                submitted = answers.get(str(question["id"]), [])
                details.append({"question": question, "submitted": submitted, "correct": submitted == question["answer"]})
            return render_template("mock_result.html", exam=exam_row, details=details)
        remaining = mock_remaining_seconds(exam_row)
        if exam_row["status"] == "active" and remaining <= 0:
            finalize_mock_exam(conn, exam_row, project_id, json_object(exam_row["answers_json"]))
            return redirect(url_for("mock.exam", exam_id=exam_id))
        token = exam_row["control_token"]
        if request.method == "GET":
            token = issue_control_token(conn, "mock_exams", exam_id)
        if request.method == "POST":
            if exam_row["status"] != "active" or not control_token_matches(
                exam_row, request.form.get("control_token")
            ):
                return session_conflict_response()
            saved = json_object(exam_row["answers_json"])
            answers = merge_mock_form_answers(questions_list, saved, request.form)
            finalize_mock_exam(conn, exam_row, project_id, answers)
            return redirect(url_for("mock.exam", exam_id=exam_id))
        return render_template(
            "mock_exam.html",
            exam=exam_row,
            questions=questions_list,
            saved_answers=json_object(exam_row["answers_json"]),
            remaining_seconds=remaining,
            control_token=token,
        )


@bp.post("/api/mock/<exam_id>/answers")
def save_answer(exam_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        exam_row = conn.execute(
            "SELECT * FROM mock_exams WHERE id=? AND project_id=?", (exam_id, project_id)
        ).fetchone()
        if not exam_row:
            abort(404)
        payload = request.get_json(silent=True) or {}
        if exam_row["status"] != "active" or not control_token_matches(exam_row, payload.get("control_token")):
            return session_conflict_response()
        if mock_remaining_seconds(exam_row) <= 0:
            finalize_mock_exam(conn, exam_row, project_id, json_object(exam_row["answers_json"]))
            return {"status": "submitted", "redirect": url_for("mock.exam", exam_id=exam_id)}, 409
        if payload.get("heartbeat"):
            return {"status": "active", "remaining_seconds": mock_remaining_seconds(exam_row)}
        try:
            question_id = int(payload.get("question_id"))
        except (TypeError, ValueError):
            abort(400)
        if question_id not in set(json.loads(exam_row["question_ids_json"])):
            abort(400)
        question = get_question(conn, question_id, project_id)
        answers = json_object(exam_row["answers_json"])
        answers[str(question_id)] = normalize_mock_answer(question, payload.get("answers", []))
        saved_at = now_iso()
        conn.execute(
            "UPDATE mock_exams SET answers_json=?,updated_at=? WHERE id=?",
            (json.dumps(answers, ensure_ascii=False), saved_at, exam_id),
        )
        return {"status": "saved", "saved_at": saved_at}


@bp.post("/mock/<exam_id>/pause")
def pause(exam_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        exam_row = conn.execute(
            "SELECT * FROM mock_exams WHERE id=? AND project_id=?", (exam_id, project_id)
        ).fetchone()
        if not exam_row:
            abort(404)
        if exam_row["status"] != "active" or not control_token_matches(
            exam_row, request.form.get("control_token")
        ):
            return session_conflict_response()
        ids = json.loads(exam_row["question_ids_json"])
        questions_list = [get_question(conn, qid, project_id) for qid in ids]
        answers = merge_mock_form_answers(questions_list, json_object(exam_row["answers_json"]), request.form)
        remaining = mock_remaining_seconds(exam_row)
        if remaining <= 0:
            finalize_mock_exam(conn, exam_row, project_id, answers)
            return redirect(url_for("mock.exam", exam_id=exam_id))
        now = now_iso()
        conn.execute(
            """UPDATE mock_exams SET status='paused',remaining_seconds=?,answers_json=?,
               active_started_at=NULL,paused_at=?,updated_at=? WHERE id=?""",
            (remaining, json.dumps(answers, ensure_ascii=False), now, now, exam_id),
        )
    return redirect(url_for("mock.exam", exam_id=exam_id))


@bp.post("/mock/<exam_id>/resume")
def resume(exam_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        exam_row = conn.execute(
            "SELECT * FROM mock_exams WHERE id=? AND project_id=?", (exam_id, project_id)
        ).fetchone()
        if not exam_row:
            abort(404)
        if exam_row["status"] != "paused" or not control_token_matches(
            exam_row, request.form.get("control_token")
        ):
            return session_conflict_response()
        now = now_iso()
        conn.execute(
            """UPDATE mock_exams SET status='active',active_started_at=?,paused_at=NULL,updated_at=?
               WHERE id=?""",
            (now, now, exam_id),
        )
    return redirect(url_for("mock.exam", exam_id=exam_id))


@bp.post("/mock/<exam_id>/terminate")
def terminate(exam_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        exam_row = conn.execute(
            "SELECT * FROM mock_exams WHERE id=? AND project_id=?", (exam_id, project_id)
        ).fetchone()
        if not exam_row:
            abort(404)
        takeover = request.form.get("takeover") == "1"
        if exam_row["status"] not in ACTIVE_SESSION_STATUSES:
            return redirect(url_for("mock.exam", exam_id=exam_id))
        if not takeover and not control_token_matches(exam_row, request.form.get("control_token")):
            return session_conflict_response()
        now = now_iso()
        conn.execute(
            """UPDATE mock_exams SET status='terminated',terminated_at=?,updated_at=?,answers_json='{}',
               remaining_seconds=0,active_started_at=NULL,control_token='' WHERE id=?""",
            (now, now, exam_id),
        )
    return redirect(url_for("mock.exam", exam_id=exam_id))
