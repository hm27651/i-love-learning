from __future__ import annotations

import json

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app import db
from services.core.common_service import now_iso
from services.core.project_service import current_project, id_belongs_to_project
from services.questions.question_service import (
    OBJECTIVE_TYPES,
    get_question,
    parse_answer,
    parse_options,
    question_count,
    question_rows,
)
from services.core.storage_service import save_image


bp = Blueprint("questions", __name__)


def _question_filter(project_id: int):
    where, params = ["s.project_id=?"], [project_id]
    if request.args.get("q"):
        where.append("(q.stem LIKE ? OR q.explanation LIKE ?)")
        term = f"%{request.args['q']}%"
        params += [term, term]
    for key, column in (
        ("status", "q.status"),
        ("type", "q.type"),
        ("subject_id", "s.id"),
        ("chapter_id", "c.id"),
        ("knowledge_point_id", "kp.id"),
    ):
        if request.args.get(key):
            where.append(f"{column}=?")
            params.append(request.args[key])
    if request.args.get("import_batch_id"):
        where.append("q.import_batch_id=?")
        params.append(request.args["import_batch_id"])
    if request.args.get("classification") == "uncategorized":
        where.append("(c.name='待分类' OR kp.name='待分类')")
    return " AND ".join(where), params


@bp.get("/questions")
def index():
    with db() as conn:
        project_id = current_project(conn)["id"]
        where, params = _question_filter(project_id)
        page = max(1, request.args.get("page", 1, type=int))
        per_page = 50
        total = question_count(conn, where, params)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        subjects = conn.execute("SELECT id,name,code exam_code FROM subjects WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        chapters = conn.execute(
            "SELECT c.id,c.name FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY c.id",
            (project_id,),
        ).fetchall()
        points = conn.execute("""SELECT kp.id,kp.name,c.name chapter_name,s.name subject_name FROM knowledge_points kp
          JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
          WHERE s.project_id=? ORDER BY s.id,c.id,kp.id""", (project_id,)).fetchall()
        rows = question_rows(conn, where, params, limit=per_page, offset=(page - 1) * per_page)
        return render_template(
            "questions.html",
            questions=rows,
            subjects=subjects,
            chapters=chapters,
            points=points,
            total=total,
            page=page,
            pages=pages,
        )


@bp.route("/questions/new", methods=["GET", "POST"])
@bp.route("/questions/<int:question_id>/edit", methods=["GET", "POST"])
def form(question_id=None):
    with db() as conn:
        project_id = current_project(conn)["id"]
        item = get_question(conn, question_id, project_id) if question_id else None
        if request.method == "POST":
            qtype = request.form["type"]
            options = parse_options(request.form.get("options", "")) if qtype in OBJECTIVE_TYPES else []
            if qtype in {"single", "multiple"} and len(options) < 2:
                flash("单选和多选至少需要两个选项", "error")
            else:
                if not id_belongs_to_project(conn, "point", request.form["knowledge_point_id"], project_id):
                    abort(404)
                try:
                    image = save_image(request.files.get("image"))
                except ValueError as exc:
                    flash(str(exc), "error")
                    image = None
                values = (
                    request.form["knowledge_point_id"],
                    qtype,
                    request.form["stem"].strip(),
                    json.dumps(options, ensure_ascii=False),
                    json.dumps(parse_answer(qtype, request.form.get("answer", "")), ensure_ascii=False),
                    request.form.get("explanation", "").strip(),
                    int(request.form.get("difficulty", 2)),
                    request.form.get("source", "").strip(),
                    request.form.get("version_note", "").strip(),
                    image or (item["image_path"] if item else None),
                    request.form.get("status", "draft"),
                    now_iso(),
                )
                if item:
                    conn.execute(
                        """UPDATE questions SET knowledge_point_id=?,type=?,stem=?,options_json=?,answer_json=?,
                           explanation=?,difficulty=?,source=?,version_note=?,image_path=?,status=?,updated_at=? WHERE id=?""",
                        values + (question_id,),
                    )
                else:
                    conn.execute(
                        """INSERT INTO questions(knowledge_point_id,type,stem,options_json,answer_json,explanation,
                           difficulty,source,version_note,image_path,status,created_at,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        values[:-1] + (values[-1], values[-1]),
                    )
                flash("题目已保存", "success")
                return redirect(url_for("questions.index"))
        points = conn.execute("""SELECT kp.id,kp.name,c.name chapter_name,s.name subject_name FROM knowledge_points kp
          JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
          WHERE s.project_id=? ORDER BY s.id,c.id,kp.id""", (project_id,)).fetchall()
        return render_template("question_form.html", item=item, points=points)


@bp.post("/questions/<int:question_id>/status/<status>")
def status(question_id, status):
    if status not in {"draft", "verified", "archived"}:
        abort(400)
    with db() as conn:
        project_id = current_project(conn)["id"]
        if not id_belongs_to_project(conn, "question", question_id, project_id):
            abort(404)
        conn.execute("UPDATE questions SET status=?,updated_at=? WHERE id=?", (status, now_iso(), question_id))
    return redirect(url_for("questions.index"))


@bp.post("/questions/bulk")
def bulk():
    action = request.form.get("action")
    ids = [int(value) for value in request.form.getlist("question_id") if value.isdigit()]
    return_to = request.form.get("return_to", "")
    destination = return_to if return_to.startswith("/") and not return_to.startswith("//") else url_for("questions.index")
    if action not in {"draft", "verified", "archived", "move"} or not ids:
        flash("请先选择题目和批量操作", "error")
        return redirect(destination)
    with db() as conn:
        project_id = current_project(conn)["id"]
        valid_ids = [value for value in ids if id_belongs_to_project(conn, "question", value, project_id)]
        if valid_ids and action == "move":
            point_id = request.form.get("target_point_id", type=int)
            if not point_id or not id_belongs_to_project(conn, "point", point_id, project_id):
                flash("请选择当前项目中的目标知识点", "error")
                return redirect(destination)
            conn.execute(
                "UPDATE questions SET knowledge_point_id=?,updated_at=? WHERE id IN (%s)" % ",".join("?" for _ in valid_ids),
                [point_id, now_iso(), *valid_ids],
            )
        elif valid_ids:
            conn.execute(
                "UPDATE questions SET status=?,updated_at=? WHERE id IN (%s)" % ",".join("?" for _ in valid_ids),
                [action, now_iso(), *valid_ids],
            )
    flash(f"已更新 {len(valid_ids)} 道题目", "success")
    return redirect(destination)


@bp.route("/questions/review/<int:question_id>", methods=["GET", "POST"])
def review(question_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        current = get_question(conn, question_id, project_id)
        if request.method == "POST":
            action = request.form.get("action")
            if action in {"draft", "verified", "archived"}:
                conn.execute("UPDATE questions SET status=?,updated_at=? WHERE id=?", (action, now_iso(), question_id))
                flash("审核状态已更新", "success")
            next_id = request.form.get("next_id", type=int)
            if next_id:
                return redirect(url_for("questions.review", question_id=next_id, **request.args))
            return redirect(url_for("questions.index", **request.args))

        where, params = _question_filter(project_id)
        page = max(1, request.args.get("page", 1, type=int))
        queue = question_rows(conn, where, params, limit=50, offset=(page - 1) * 50)
        ids = [item["id"] for item in queue]
        if question_id in ids:
            position = ids.index(question_id)
            previous_id = ids[position - 1] if position else None
            next_id = ids[position + 1] if position + 1 < len(ids) else None
        else:
            previous_id = next_id = None
            queue = [current]
            position = 0
        return render_template(
            "question_review.html",
            question=current,
            queue=queue,
            position=position,
            previous_id=previous_id,
            next_id=next_id,
        )
