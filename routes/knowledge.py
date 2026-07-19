from __future__ import annotations

import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from services.core.runtime_service import backup_dir, db, db_path
from knowledge_service import (
    KnowledgeDeleteError,
    analyze_delete,
    duplicate_groups,
    perform_delete,
    sibling_name_exists,
)
from services.core.project_service import current_project, id_belongs_to_project


bp = Blueprint("knowledge", __name__)


@bp.route("/knowledge", methods=["GET", "POST"])
def index():
    with db() as conn:
        project = current_project(conn); project_id = project["id"]
        if request.method == "POST":
            kind = request.form["kind"]
            name = request.form.get("name", "").strip()
            created_id = None
            if not name:
                flash("名称不能为空", "error")
            elif kind == "subject":
                try:
                    created_id = conn.execute(
                        "INSERT INTO subjects(project_id,name,code) VALUES (?,?,?)",
                        (project_id, name, request.form.get("exam_code", "").strip()),
                    ).lastrowid
                except sqlite3.IntegrityError:
                    flash("同一项目内科目代码不能重复", "error")
                    return redirect(url_for("knowledge.index"))
            elif kind == "chapter":
                if not id_belongs_to_project(conn, "subject", request.form["subject_id"], project_id): abort(404)
                subject_id = int(request.form["subject_id"])
                if sibling_name_exists(conn, "chapter", subject_id, name):
                    flash("同一科目内不能存在同名章节", "error")
                    return redirect(url_for("knowledge.index"))
                created_id = conn.execute(
                    "INSERT INTO chapters(subject_id,name,is_core) VALUES (?,?,?)",
                    (subject_id, name, int("is_core" in request.form)),
                ).lastrowid
            elif kind == "point":
                if not id_belongs_to_project(conn, "chapter", request.form["chapter_id"], project_id): abort(404)
                chapter_id = int(request.form["chapter_id"])
                if sibling_name_exists(conn, "point", chapter_id, name):
                    flash("同一章节内不能存在同名知识点", "error")
                    return redirect(url_for("knowledge.index"))
                created_id = conn.execute(
                    "INSERT INTO knowledge_points(chapter_id,name) VALUES (?,?)", (chapter_id, name)
                ).lastrowid
            flash("知识树已更新", "success")
            if created_id:
                return redirect(url_for("knowledge.index", focus_kind=kind, focus_id=created_id))
            return redirect(url_for("knowledge.index"))
        subjects = conn.execute("""SELECT s.id,s.project_id,s.name,s.code exam_code,
          (SELECT COUNT(*) FROM chapters c WHERE c.subject_id=s.id) chapter_count,
          (SELECT COUNT(*) FROM knowledge_points kp JOIN chapters c ON c.id=kp.chapter_id
            WHERE c.subject_id=s.id) point_count,
          (SELECT COUNT(*) FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
            JOIN chapters c ON c.id=kp.chapter_id WHERE c.subject_id=s.id) question_count
          FROM subjects s WHERE s.project_id=? ORDER BY s.id""", (project_id,)).fetchall()
        chapters = conn.execute("""SELECT c.*,s.name subject_name,
          (SELECT COUNT(*) FROM knowledge_points kp WHERE kp.chapter_id=c.id) point_count,
          (SELECT COUNT(*) FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
            WHERE kp.chapter_id=c.id) question_count
          FROM chapters c JOIN subjects s ON s.id=c.subject_id
          WHERE s.project_id=? ORDER BY s.id,c.id""", (project_id,)).fetchall()
        points = conn.execute("""SELECT kp.*,c.name chapter_name,
          (SELECT COUNT(*) FROM questions q WHERE q.knowledge_point_id=kp.id) question_count
          FROM knowledge_points kp JOIN chapters c ON c.id=kp.chapter_id
          JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY c.id,kp.id""", (project_id,)).fetchall()
        overview = {
            "subjects": len(subjects),
            "chapters": len(chapters),
            "points": len(points),
            "questions": sum(row["question_count"] for row in points),
        }
        return render_template("knowledge.html", subjects=subjects, chapters=chapters, points=points, project=project,
                               overview=overview, duplicate_groups=duplicate_groups(conn, project_id))


@bp.post("/knowledge/chapter/<int:chapter_id>/toggle-core")
def toggle_core(chapter_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        if not id_belongs_to_project(conn, "chapter", chapter_id, project_id): abort(404)
        conn.execute("UPDATE chapters SET is_core=1-is_core WHERE id=?", (chapter_id,))
    return redirect(url_for("knowledge.index", focus_kind="chapter", focus_id=chapter_id))


@bp.post("/knowledge/rename")
def rename():
    allowed = {"subject": "subjects", "chapter": "chapters", "point": "knowledge_points"}
    kind = request.form.get("kind")
    name = request.form.get("name", "").strip()
    if kind not in allowed or not name: abort(400)
    with db() as conn:
        project_id = current_project(conn)["id"]
        if not id_belongs_to_project(conn, kind, request.form["id"], project_id): abort(404)
        if kind == "subject":
            try:
                conn.execute("UPDATE subjects SET name=?,code=? WHERE id=?", (name, request.form.get("exam_code", "").strip(), request.form["id"]))
            except sqlite3.IntegrityError:
                flash("同一项目内科目代码不能重复", "error")
                return redirect(url_for("knowledge.index"))
        else:
            if kind == "chapter":
                row = conn.execute("SELECT subject_id FROM chapters WHERE id=?", (request.form["id"],)).fetchone()
                if sibling_name_exists(conn, "chapter", row["subject_id"], name, int(request.form["id"])):
                    flash("同一科目内不能存在同名章节", "error")
                    return redirect(url_for("knowledge.index"))
                conn.execute(
                    "UPDATE chapters SET name=?,is_core=? WHERE id=?",
                    (name, int("is_core" in request.form), request.form["id"]),
                )
            else:
                row = conn.execute("SELECT chapter_id FROM knowledge_points WHERE id=?", (request.form["id"],)).fetchone()
                if sibling_name_exists(conn, "point", row["chapter_id"], name, int(request.form["id"])):
                    flash("同一章节内不能存在同名知识点", "error")
                    return redirect(url_for("knowledge.index"))
                conn.execute("UPDATE knowledge_points SET name=? WHERE id=?", (name, request.form["id"]))
    flash("名称已更新", "success")
    return redirect(url_for("knowledge.index", focus_kind=kind, focus_id=request.form["id"]))


@bp.get("/api/knowledge/<kind>/<int:node_id>/delete-impact")
def delete_impact(kind, node_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        try:
            impact = analyze_delete(conn, project_id, kind, node_id, request.args.get("target_id", type=int))
        except KnowledgeDeleteError as exc:
            return {"error": str(exc)}, 400
        return impact


@bp.post("/knowledge/delete")
def delete():
    kind = request.form.get("kind", "")
    node_id = request.form.get("node_id", type=int)
    target_id = request.form.get("target_id", type=int)
    if not node_id:
        abort(400)
    with db() as conn:
        project_id = current_project(conn)["id"]
        try:
            result = perform_delete(
                conn,
                project_id,
                kind,
                node_id,
                target_id=target_id,
                merge_conflicts=request.form.get("merge_conflicts") == "1",
                confirmation_name=request.form.get("confirmation_name", ""),
                db_path=db_path(),
                backup_dir=backup_dir(),
            )
        except KnowledgeDeleteError as exc:
            flash(str(exc), "error")
            return redirect(url_for("knowledge.index"))
        except (sqlite3.Error, OSError) as exc:
            flash(f"删除未完成，所有数据库修改均已回滚：{exc}", "error")
            return redirect(url_for("knowledge.index"))
    backup = result["backup_folder"]
    message = f"已删除“{result['source']['name']}”"
    if result["target"]:
        message += f"，内容已迁移到“{result['target']['name']}”"
    if backup:
        message += f"；备份：{backup}"
    flash(message, "success")
    if result["target"]:
        return redirect(url_for("knowledge.index", focus_kind=kind, focus_id=result["target"]["id"]))
    return redirect(url_for("knowledge.index"))

