from __future__ import annotations

import re
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from services.core.runtime_service import backup_data_snapshot, data_dir, db
from services.core.common_service import now_iso
from migrations import PROJECT_MODULE_DEFAULTS, create_project
from services.core.project_service import current_project, module_enabled, project_modules, seed_project_weeks


bp = Blueprint("projects", __name__)


@bp.post("/projects/switch")
def switch():
    destination = request.form.get("return_to", "/")
    if not destination.startswith("/") or destination.startswith("//"):
        destination = url_for("dashboard")
    if re.match(r"^/(?:questions/(?:review/)?\d+|practice/[0-9a-f-]+|mock/[0-9a-f-]+|labs/\d+)", destination):
        destination = url_for("dashboard")
    with db() as conn:
        project = conn.execute(
            "SELECT id FROM learning_projects WHERE id=? AND status='active'",
            (request.form.get("project_id"),),
        ).fetchone()
        if not project:
            flash("该学习项目不可用，已切换到可用项目", "error")
            session.pop("current_project_id", None)
            current_project(conn)
        else:
            session["current_project_id"] = project["id"]
    return redirect(destination)


@bp.get("/projects")
def index():
    with db() as conn:
        rows = conn.execute("""SELECT p.*,
          (SELECT COUNT(*) FROM subjects s WHERE s.project_id=p.id) subject_count,
          (SELECT COUNT(*) FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
           JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=p.id) question_count
          FROM learning_projects p ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,p.id""").fetchall()
        return render_template("projects.html", projects=rows)


@bp.route("/projects/new", methods=["GET", "POST"])
@bp.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
def form(project_id=None):
    with db() as conn:
        item = conn.execute("SELECT * FROM learning_projects WHERE id=?", (project_id,)).fetchone() if project_id else None
        if project_id and not item:
            abort(404)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            project_type = request.form.get("project_type", "practice")
            if not name or project_type not in PROJECT_MODULE_DEFAULTS:
                flash("请填写项目名称并选择有效模板", "error")
            else:
                duration = max(1, min(104, request.form.get("duration_weeks", 12, type=int) or 12))
                start_date = request.form.get("start_date") or date.today().isoformat()
                alias = request.form.get("practice_alias", "实践任务").strip() or "实践任务"
                if item:
                    conn.execute("""UPDATE learning_projects SET name=?,project_type=?,description=?,start_date=?,
                      duration_weeks=?,practice_alias=?,updated_at=? WHERE id=?""",
                      (name, project_type, request.form.get("description", "").strip(), start_date,
                       duration, alias, now_iso(), project_id))
                    target_id = project_id
                else:
                    target_id = create_project(conn, name, project_type, start_date, duration, alias)
                    conn.execute("UPDATE learning_projects SET description=? WHERE id=?", (request.form.get("description", "").strip(), target_id))
                defaults = PROJECT_MODULE_DEFAULTS[project_type]
                for key in defaults:
                    enabled = int(request.form.get(f"module_{key}") == "1") if "custom_modules" in request.form else defaults[key]
                    conn.execute("""INSERT INTO project_modules(project_id,module_key,enabled) VALUES (?,?,?)
                      ON CONFLICT(project_id,module_key) DO UPDATE SET enabled=excluded.enabled""", (target_id, key, enabled))
                if module_enabled(conn, target_id, "plan"):
                    seed_project_weeks(conn, target_id, duration)
                session["current_project_id"] = target_id
                flash("学习项目已保存", "success")
                return redirect(url_for("projects.index"))
        modules = project_modules(conn, project_id) if item else PROJECT_MODULE_DEFAULTS["practice"]
        return render_template("project_form.html", item=item, modules=modules)


@bp.post("/projects/<int:project_id>/archive")
def archive(project_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM learning_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            abort(404)
        active_count = conn.execute("SELECT COUNT(*) n FROM learning_projects WHERE status='active'").fetchone()["n"]
        if row["status"] == "active" and active_count <= 1:
            flash("至少保留一个启用中的学习项目", "error")
        else:
            conn.execute("UPDATE learning_projects SET status='archived',updated_at=? WHERE id=?", (now_iso(), project_id))
            if session.get("current_project_id") == project_id:
                session.pop("current_project_id", None)
                current_project(conn)
            flash("项目已归档，题目和进度均已保留", "success")
    return redirect(url_for("projects.index"))


@bp.post("/projects/<int:project_id>/restore")
def restore(project_id):
    with db() as conn:
        if not conn.execute("SELECT 1 FROM learning_projects WHERE id=?", (project_id,)).fetchone():
            abort(404)
        conn.execute("UPDATE learning_projects SET status='active',updated_at=? WHERE id=?", (now_iso(), project_id))
    flash("项目已恢复", "success")
    return redirect(url_for("projects.index"))


@bp.post("/projects/<int:project_id>/delete")
def delete(project_id):
    if request.form.get("confirmation", "").strip() != "永久删除":
        flash("请输入“永久删除”进行二次确认", "error")
        return redirect(url_for("projects.index"))
    with db() as conn:
        project = conn.execute("SELECT * FROM learning_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            abort(404)
        if conn.execute("SELECT COUNT(*) n FROM learning_projects").fetchone()["n"] <= 1:
            flash("不能永久删除最后一个学习项目", "error")
            return redirect(url_for("projects.index"))
        backup = backup_data_snapshot(conn, f"pre_delete_project_{project_id}")
        qids = [row[0] for row in conn.execute("""SELECT q.id FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
          JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=?""", (project_id,))]
        if qids:
            marks = ",".join("?" for _ in qids)
            conn.execute(f"DELETE FROM attempts WHERE question_id IN ({marks})", qids)
            conn.execute(f"DELETE FROM question_progress WHERE question_id IN ({marks})", qids)
            conn.execute(f"UPDATE import_candidates SET duplicate_question_id=NULL WHERE duplicate_question_id IN ({marks})", qids)
            conn.execute(f"DELETE FROM questions WHERE id IN ({marks})", qids)
        document_paths = [row[0] for row in conn.execute("SELECT stored_path FROM source_documents WHERE project_id=?", (project_id,))]
        conn.execute("DELETE FROM import_candidates WHERE job_id IN (SELECT id FROM import_jobs WHERE project_id=?)", (project_id,))
        conn.execute("DELETE FROM import_jobs WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM source_documents WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM mock_exams WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM practice_sessions WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM labs WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM weekly_plans WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM knowledge_points WHERE chapter_id IN (SELECT c.id FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=?)", (project_id,))
        conn.execute("DELETE FROM chapters WHERE subject_id IN (SELECT id FROM subjects WHERE project_id=?)", (project_id,))
        conn.execute("DELETE FROM subjects WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM project_modules WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM project_settings WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM learning_projects WHERE id=?", (project_id,))
        if session.get("current_project_id") == project_id:
            session.pop("current_project_id", None)
            current_project(conn)
        for stored in document_paths:
            path = data_dir() / stored
            if path.is_file():
                path.unlink()
    flash(f"项目已永久删除；删除前备份保存在 {backup}", "success")
    return redirect(url_for("projects.index"))
