from __future__ import annotations

import json
import random
import sqlite3
import uuid
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app import db
from services.core.common_service import json_object, now_iso
from services.core.project_service import current_project, id_belongs_to_project
from services.questions.question_service import (
    OBJECTIVE_TYPES,
    get_question,
    grade_objective,
    question_count,
    question_rows,
    record_attempt,
)
from services.core.session_service import (
    ACTIVE_SESSION_STATUSES,
    active_practice_session,
    control_token_matches,
    issue_control_token,
    session_conflict_response,
    session_progress,
)


bp = Blueprint("practice", __name__)


def practice_filter_clause(values, project_id):
    where, params = ["q.status='verified'", "s.project_id=?"], [project_id]
    for key, column in (
        ("subject_id", "s.id"),
        ("chapter_id", "c.id"),
        ("knowledge_point_id", "kp.id"),
        ("type", "q.type"),
    ):
        if values.get(key):
            where.append(f"{column}=?")
            params.append(values[key])
    return " AND ".join(where), params


@bp.get("/api/practice/count")
def practice_match_count():
    with db() as conn:
        project_id = current_project(conn)["id"]
        where, params = practice_filter_clause(request.args, project_id)
        return {"count": question_count(conn, where, params)}


@bp.route("/practice", methods=["GET", "POST"])
def index():
    with db() as conn:
        project_id = current_project(conn)["id"]
        active = active_practice_session(conn, project_id, "practice")
        if request.method == "POST":
            if active:
                flash("已有进行中或暂停的章节练习，请先继续或终止旧会话", "error")
                return redirect(url_for("practice.index"))
            where, params = practice_filter_clause(request.form, project_id)
            rows = question_rows(conn, where, params)
            random.shuffle(rows)
            if request.form.get("selection_mode", "count") != "all":
                requested_count = request.form.get("count", 10, type=int) or 10
                rows = rows[: max(1, min(100, requested_count))]
            if not rows:
                flash("没有符合条件的已核验题目", "error")
                return redirect(url_for("practice.index"))
            sid = uuid.uuid4().hex
            now = now_iso()
            try:
                conn.execute(
                    """INSERT INTO practice_sessions(id,project_id,mode,question_ids_json,started_at,
                       status,state_json,updated_at) VALUES (?,?,?,?,?,'active','{}',?)""",
                    (sid, project_id, "practice", json.dumps([r["id"] for r in rows]), now, now),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("已有进行中或暂停的章节练习", "error")
                return redirect(url_for("practice.index"))
            return redirect(url_for("practice.run", session_id=sid))
        subjects = conn.execute(
            "SELECT id,project_id,name,code exam_code FROM subjects WHERE project_id=? ORDER BY id",
            (project_id,),
        ).fetchall()
        chapters = conn.execute(
            """SELECT c.* FROM chapters c JOIN subjects s ON s.id=c.subject_id
               WHERE s.project_id=? ORDER BY c.id""",
            (project_id,),
        ).fetchall()
        points = conn.execute(
            """SELECT kp.* FROM knowledge_points kp JOIN chapters c ON c.id=kp.chapter_id
               JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY kp.id""",
            (project_id,),
        ).fetchall()
        return render_template(
            "practice_setup.html",
            subjects=subjects,
            chapters=chapters,
            points=points,
            active_session=session_progress(active),
        )


@bp.route("/practice/<session_id>", methods=["GET", "POST"])
def run(session_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        item = conn.execute(
            "SELECT * FROM practice_sessions WHERE id=? AND project_id=?", (session_id, project_id)
        ).fetchone()
        if not item:
            abort(404)
        ids = json.loads(item["question_ids_json"])
        index = item["current_index"]
        answered_count = conn.execute(
            "SELECT COUNT(*) n FROM attempts WHERE session_id=?", (session_id,)
        ).fetchone()["n"]
        if item["status"] == "terminated":
            return render_template(
                "session_terminated.html",
                kind="practice",
                mode=item["mode"],
                answered_count=answered_count,
                total=len(ids),
            )
        if item["status"] == "completed":
            return render_template("practice_complete.html", total=len(ids), mode=item["mode"])
        if index >= len(ids):
            now = now_iso()
            conn.execute(
                """UPDATE practice_sessions SET status='completed',completed_at=COALESCE(completed_at,?),
                   updated_at=?,control_token='' WHERE id=?""",
                (now, now, session_id),
            )
            return render_template("practice_complete.html", total=len(ids), mode=item["mode"])
        question = get_question(conn, ids[index], project_id)
        state = json_object(item["state_json"])
        feedback = state.get("feedback")
        token = item["control_token"]
        if request.method == "GET" and item["status"] in ACTIVE_SESSION_STATUSES:
            token = issue_control_token(conn, "practice_sessions", session_id)
        if request.method == "POST":
            if item["status"] != "active" or not control_token_matches(
                item, request.form.get("control_token")
            ):
                return session_conflict_response()
            if request.form.get("action") == "next":
                if not feedback:
                    abort(400)
                conn.execute(
                    """UPDATE practice_sessions SET current_index=current_index+1,state_json='{}',
                       updated_at=? WHERE id=?""",
                    (now_iso(), session_id),
                )
                return redirect(url_for("practice.run", session_id=session_id))
            rating = request.form.get("rating")
            if feedback and not (feedback.get("reveal") and rating):
                abort(409)
            if question["type"] in OBJECTIVE_TYPES:
                correct, submitted = grade_objective(question, request.form)
                progress_change = record_attempt(
                    conn, question, item["mode"], is_correct=correct, session_id=session_id
                )
                feedback = {"correct": correct, "submitted": submitted, "progress": progress_change}
                state = {"selected": submitted, "draft": "", "feedback": feedback}
            else:
                if rating:
                    progress_change = record_attempt(
                        conn, question, item["mode"], rating=rating, session_id=session_id
                    )
                    feedback = {"rating": rating, "progress": progress_change}
                    state = {
                        "selected": [],
                        "draft": state.get("draft", request.form.get("draft", "")),
                        "feedback": feedback,
                    }
                else:
                    feedback = {"reveal": True}
                    state = {"selected": [], "draft": request.form.get("draft", ""), "feedback": feedback}
            conn.execute(
                "UPDATE practice_sessions SET state_json=?,updated_at=? WHERE id=?",
                (json.dumps(state, ensure_ascii=False), now_iso(), session_id),
            )
        return render_template(
            "practice_question.html",
            question=question,
            feedback=feedback,
            index=index,
            total=len(ids),
            session_id=session_id,
            mode=item["mode"],
            session_status=item["status"],
            control_token=token,
            saved_state=state,
        )


@bp.post("/practice/<session_id>/pause")
def pause(session_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        item = conn.execute(
            "SELECT * FROM practice_sessions WHERE id=? AND project_id=?", (session_id, project_id)
        ).fetchone()
        if not item:
            abort(404)
        payload = request.get_json(silent=True) or request.form
        if item["status"] != "active" or not control_token_matches(item, payload.get("control_token")):
            return session_conflict_response()
        state = json_object(item["state_json"])
        if not state.get("feedback"):
            selected = payload.get("selected", [])
            if not isinstance(selected, list):
                selected = [selected]
            state.update({"selected": [str(value) for value in selected], "draft": str(payload.get("draft", ""))})
        now = now_iso()
        conn.execute(
            """UPDATE practice_sessions SET status='paused',state_json=?,paused_at=?,updated_at=?
               WHERE id=?""",
            (json.dumps(state, ensure_ascii=False), now, now, session_id),
        )
        return {"status": "paused", "redirect": url_for("practice.run", session_id=session_id)}


@bp.post("/practice/<session_id>/resume")
def resume(session_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        item = conn.execute(
            "SELECT * FROM practice_sessions WHERE id=? AND project_id=?", (session_id, project_id)
        ).fetchone()
        if not item:
            abort(404)
        if item["status"] != "paused" or not control_token_matches(item, request.form.get("control_token")):
            return session_conflict_response()
        conn.execute(
            "UPDATE practice_sessions SET status='active',paused_at=NULL,updated_at=? WHERE id=?",
            (now_iso(), session_id),
        )
    return redirect(url_for("practice.run", session_id=session_id))


@bp.post("/practice/<session_id>/terminate")
def terminate(session_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        item = conn.execute(
            "SELECT * FROM practice_sessions WHERE id=? AND project_id=?", (session_id, project_id)
        ).fetchone()
        if not item:
            abort(404)
        takeover = request.form.get("takeover") == "1"
        if item["status"] not in ACTIVE_SESSION_STATUSES:
            return redirect(url_for("practice.run", session_id=session_id))
        if not takeover and not control_token_matches(item, request.form.get("control_token")):
            return session_conflict_response()
        now = now_iso()
        conn.execute(
            """UPDATE practice_sessions SET status='terminated',terminated_at=?,updated_at=?,
               control_token='' WHERE id=?""",
            (now, now, session_id),
        )
    return redirect(url_for("practice.run", session_id=session_id))


@bp.route("/review", methods=["GET", "POST"])
def review():
    with db() as conn:
        project_id = current_project(conn)["id"]
        active = active_practice_session(conn, project_id, "review")
        where = "q.status='verified' AND p.attempts>0 AND s.project_id=?"
        params = [project_id]
        mode = request.args.get("filter", "due")
        if mode == "due":
            where += " AND p.due_date<=?"
            params.append(date.today().isoformat())
        elif mode == "errors":
            where += " AND p.error_count>0"
        if request.args.get("chapter_id"):
            where += " AND c.id=?"
            params.append(request.args["chapter_id"])
        rows = question_rows(conn, where, params)
        rows.sort(key=lambda item: (item["due_date"] or "9999-12-31", -item["error_count"], item["mastery_level"]))
        if request.method == "POST":
            if active:
                flash("已有进行中或暂停的错题复习，请先继续或终止旧会话", "error")
                return redirect(url_for("practice.review"))
            ids = [int(x) for x in request.form.getlist("question_id")]
            if not ids:
                flash("请选择至少一道题", "error")
                return redirect(request.url)
            sid = uuid.uuid4().hex
            valid_ids = [value for value in ids if id_belongs_to_project(conn, "question", value, project_id)]
            if not valid_ids:
                abort(400)
            now = now_iso()
            try:
                conn.execute(
                    """INSERT INTO practice_sessions(id,project_id,mode,question_ids_json,started_at,
                       status,state_json,updated_at) VALUES (?,?,?,?,?,'active','{}',?)""",
                    (sid, project_id, "review", json.dumps(valid_ids), now, now),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("已有进行中或暂停的错题复习", "error")
                return redirect(url_for("practice.review"))
            return redirect(url_for("practice.run", session_id=sid))
        chapters = conn.execute(
            """SELECT c.id,c.name FROM chapters c JOIN subjects s ON s.id=c.subject_id
               WHERE s.project_id=? ORDER BY c.id""",
            (project_id,),
        ).fetchall()
        counts = {
            "due": conn.execute(
                """SELECT COUNT(*) n FROM question_progress p JOIN questions q ON q.id=p.question_id
                   JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
                   JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND q.status='verified' AND p.due_date<=?""",
                (project_id, date.today().isoformat()),
            ).fetchone()["n"],
            "errors": conn.execute(
                """SELECT COUNT(*) n FROM question_progress p JOIN questions q ON q.id=p.question_id
                   JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
                   JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND q.status='verified' AND p.error_count>0""",
                (project_id,),
            ).fetchone()["n"],
        }
        return render_template(
            "review.html",
            questions=rows,
            chapters=chapters,
            filter=mode,
            counts=counts,
            active_session=session_progress(active),
        )
