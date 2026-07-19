from __future__ import annotations

from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from migrations import PROJECT_MODULE_DEFAULTS
from services.core.common_service import now_iso
from services.core.project_service import current_project, seed_project_weeks
from services.core.runtime_service import db


bp = Blueprint("onboarding", __name__)

TEMPLATE_DETAILS = {
    "practice": {
        "name": "普通刷题",
        "summary": "保留题库、练习、复习和学习进度，界面最轻量。",
        "modules": "核心刷题功能",
    },
    "exam_prep": {
        "name": "考试备考",
        "summary": "增加模拟考试、周计划和准备度判断。",
        "modules": "核心功能 + 模拟 + 计划 + 准备度",
    },
    "practical_certification": {
        "name": "实操认证",
        "summary": "在考试备考基础上增加实验或实践任务。",
        "modules": "核心功能 + 模拟 + 计划 + 实践 + 准备度",
    },
}


def onboarding_required(conn) -> bool:
    projects = conn.execute("SELECT id FROM learning_projects WHERE status='active' ORDER BY id").fetchall()
    if len(projects) != 1:
        return False
    project_id = projects[0]["id"]
    completed = conn.execute(
        "SELECT value FROM project_settings WHERE project_id=? AND key='onboarding_completed'",
        (project_id,),
    ).fetchone()
    if completed and completed["value"] == "1":
        return False
    counts = {
        "questions": conn.execute(
            """SELECT COUNT(*) n FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
              JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
              WHERE s.project_id=?""",
            (project_id,),
        ).fetchone()["n"],
        "attempts": conn.execute("SELECT COUNT(*) n FROM attempts").fetchone()["n"],
        "sources": conn.execute("SELECT COUNT(*) n FROM source_documents").fetchone()["n"],
    }
    return not any(counts.values())


def require_onboarding():
    if current_app.config.get("TESTING") and not current_app.config.get("ONBOARDING_IN_TESTS"):
        return None
    if request.endpoint in {"onboarding.welcome", "data_management.health", "static"}:
        return None
    with db() as conn:
        if onboarding_required(conn):
            return redirect(url_for("onboarding.welcome"))
    return None


@bp.route("/welcome", methods=["GET", "POST"])
def welcome():
    with db() as conn:
        project = current_project(conn)
        if request.method == "POST":
            if request.form.get("action") == "skip":
                conn.execute(
                    """INSERT INTO project_settings(project_id,key,value) VALUES (?,'onboarding_completed','1')
                      ON CONFLICT(project_id,key) DO UPDATE SET value='1'""",
                    (project["id"],),
                )
                flash("已保留当前空项目，可以稍后在项目管理中调整模块。", "success")
                return redirect(url_for("dashboard"))

            project_type = request.form.get("project_type", "practice")
            name = request.form.get("name", "").strip()
            if project_type not in PROJECT_MODULE_DEFAULTS or not name:
                flash("请填写项目名称并选择有效模板。", "error")
            else:
                duration = max(1, min(104, request.form.get("duration_weeks", 12, type=int) or 12))
                alias = request.form.get("practice_alias", "实践任务").strip() or "实践任务"
                start_date = request.form.get("start_date") or date.today().isoformat()
                conn.execute(
                    """UPDATE learning_projects SET name=?,project_type=?,start_date=?,duration_weeks=?,
                      practice_alias=?,updated_at=? WHERE id=?""",
                    (name, project_type, start_date, duration, alias, now_iso(), project["id"]),
                )
                for module_key, enabled in PROJECT_MODULE_DEFAULTS[project_type].items():
                    conn.execute(
                        """INSERT INTO project_modules(project_id,module_key,enabled) VALUES (?,?,?)
                          ON CONFLICT(project_id,module_key) DO UPDATE SET enabled=excluded.enabled""",
                        (project["id"], module_key, enabled),
                    )
                conn.execute(
                    """INSERT INTO project_settings(project_id,key,value) VALUES (?,'onboarding_completed','1')
                      ON CONFLICT(project_id,key) DO UPDATE SET value='1'""",
                    (project["id"],),
                )
                if PROJECT_MODULE_DEFAULTS[project_type]["plan"]:
                    seed_project_weeks(conn, project["id"], duration)
                session["current_project_id"] = project["id"]
                flash("学习项目已准备好，下一步可以建立知识树或导入题库。", "success")
                return redirect(url_for("dashboard"))
        return render_template(
            "welcome.html",
            project=project,
            templates=TEMPLATE_DETAILS,
            today=date.today().isoformat(),
        )
