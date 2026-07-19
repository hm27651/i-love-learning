from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from services.core.runtime_service import db
from services.core.common_service import now_iso
from services.core.project_service import current_project, id_belongs_to_project, module_disabled, module_enabled
from services.core.storage_service import save_image


bp = Blueprint("labs", __name__)


@bp.route("/labs")
def index():
    with db() as conn:
        project = current_project(conn)
        project_id = project["id"]
        if not module_enabled(conn, project_id, "tasks"):
            return module_disabled(project, project["practice_alias"])
        rows = conn.execute(
            """SELECT l.*,c.name chapter_name,s.name subject_name FROM labs l
               JOIN chapters c ON c.id=l.chapter_id JOIN subjects s ON s.id=c.subject_id
               WHERE l.project_id=?
               ORDER BY CASE l.status WHEN 'doing' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END,l.due_date,l.id""",
            (project_id,),
        ).fetchall()
        return render_template("labs.html", labs=rows, task_alias=project["practice_alias"])


@bp.route("/labs/new", methods=["GET", "POST"])
@bp.route("/labs/<int:lab_id>/edit", methods=["GET", "POST"])
def form(lab_id=None):
    with db() as conn:
        project = current_project(conn)
        project_id = project["id"]
        if not module_enabled(conn, project_id, "tasks"):
            return module_disabled(project, project["practice_alias"])
        item = (
            conn.execute("SELECT * FROM labs WHERE id=? AND project_id=?", (lab_id, project_id)).fetchone()
            if lab_id
            else None
        )
        if lab_id and not item:
            abort(404)
        if request.method == "POST":
            if not id_belongs_to_project(conn, "chapter", request.form["chapter_id"], project_id):
                abort(404)
            try:
                image = save_image(request.files.get("image"))
            except ValueError as exc:
                flash(str(exc), "error")
                image = None
            values = (
                request.form["chapter_id"],
                request.form["title"].strip(),
                request.form.get("objective", "").strip(),
                request.form.get("topology_file_path", "").strip(),
                request.form.get("commands", "").strip(),
                request.form.get("verification", "").strip(),
                request.form.get("result", "").strip(),
                image or (item["image_path"] if item else None),
                request.form.get("status", "planned"),
                request.form.get("due_date") or None,
                now_iso(),
            )
            if item:
                conn.execute(
                    """UPDATE labs SET chapter_id=?,title=?,objective=?,topology_file_path=?,commands=?,
                       verification=?,result=?,image_path=?,status=?,due_date=?,updated_at=? WHERE id=?""",
                    values + (lab_id,),
                )
            else:
                conn.execute(
                    """INSERT INTO labs(project_id,chapter_id,title,objective,topology_file_path,commands,
                       verification,result,image_path,status,due_date,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (project_id,) + values[:-1] + (values[-1], values[-1]),
                )
            flash(f"{project['practice_alias']}记录已保存", "success")
            return redirect(url_for("labs.index"))
        chapters = conn.execute(
            """SELECT c.id,c.name,s.name subject_name FROM chapters c
               JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY s.id,c.id""",
            (project_id,),
        ).fetchall()
        return render_template("lab_form.html", item=item, chapters=chapters, task_alias=project["practice_alias"])


@bp.post("/labs/<int:lab_id>/complete")
def complete(lab_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        row = conn.execute("SELECT 1 FROM labs WHERE id=? AND project_id=?", (lab_id, project_id)).fetchone()
        if not row:
            abort(404)
        conn.execute("UPDATE labs SET status='completed',updated_at=? WHERE id=?", (now_iso(), lab_id))
    return redirect(url_for("labs.index"))
