from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from services.core.runtime_service import db
from services.core.project_service import current_project, module_disabled, module_enabled, seed_project_weeks


bp = Blueprint("plans", __name__)


@bp.route("/plans", methods=["GET", "POST"])
def index():
    with db() as conn:
        project = current_project(conn)
        project_id = project["id"]
        if not module_enabled(conn, project_id, "plan"):
            return module_disabled(project, "学习计划")
        if request.method == "POST":
            week = max(1, min(project["duration_weeks"], int(request.form["week_no"])))
            conn.execute(
                """INSERT INTO weekly_plans(project_id,week_no,title,chapter_goal,question_goal,lab_goal,notes)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(project_id,week_no) DO UPDATE SET
                     title=excluded.title,
                     chapter_goal=excluded.chapter_goal,
                     question_goal=excluded.question_goal,
                     lab_goal=excluded.lab_goal,
                     notes=excluded.notes""",
                (
                    project_id,
                    week,
                    request.form["title"].strip(),
                    request.form.get("chapter_goal", "").strip(),
                    max(0, int(request.form.get("question_goal", 0))),
                    max(0, int(request.form.get("lab_goal", 0))),
                    request.form.get("notes", "").strip(),
                ),
            )
            flash(f"第 {week} 周目标已保存", "success")
            return redirect(url_for("plans.index"))
        seed_project_weeks(conn, project_id, project["duration_weeks"])
        rows = conn.execute(
            "SELECT * FROM weekly_plans WHERE project_id=? ORDER BY week_no", (project_id,)
        ).fetchall()
        completed_questions = conn.execute(
            """SELECT COUNT(*) n FROM attempts a JOIN questions q ON q.id=a.question_id
               JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
               JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=?""",
            (project_id,),
        ).fetchone()["n"]
        completed_labs = conn.execute(
            "SELECT COUNT(*) n FROM labs WHERE project_id=? AND status='completed'", (project_id,)
        ).fetchone()["n"]
        return render_template(
            "plans.html",
            plans=rows,
            completed_questions=completed_questions,
            completed_labs=completed_labs,
            project=project,
        )
