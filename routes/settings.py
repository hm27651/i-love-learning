from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for

from services.core.project_service import current_project, project_modules, project_settings, settings
from services.core.storage_service import DATA_DIR, DB_PATH, lan_url


def register_settings_routes(app, db_provider, export_queue_provider):
    @app.route("/settings", methods=["GET", "POST"], endpoint="app_settings")
    def app_settings():
        with db_provider() as conn:
            project = current_project(conn)
            project_id = project["id"]
            if request.method == "POST":
                try:
                    project_values = {
                        "mock_threshold": str(max(0, min(100, float(request.form["mock_threshold"])))),
                        "chapter_threshold": str(max(0, min(100, float(request.form["chapter_threshold"])))),
                        "task_threshold": str(max(0, min(100, float(request.form["task_threshold"])))),
                        "qualifying_count": str(max(1, min(200, int(request.form["qualifying_count"])))),
                        "qualifying_minutes": str(max(1, min(300, int(request.form["qualifying_minutes"])))),
                        "gate_mock_enabled": str(int("gate_mock_enabled" in request.form)),
                        "gate_chapter_enabled": str(int("gate_chapter_enabled" in request.form)),
                        "gate_task_enabled": str(int("gate_task_enabled" in request.form)),
                    }
                    global_values = {
                        "intervals": request.form["intervals"].strip(),
                        "max_import_mb": str(max(1, min(500, int(request.form.get("max_import_mb", 50))))),
                    }
                    intervals = [int(x) for x in global_values["intervals"].split(",")]
                    if len(intervals) != 4 or any(x < 1 for x in intervals):
                        raise ValueError
                except (ValueError, KeyError):
                    flash("设置格式不正确：复习间隔必须是 4 个逗号分隔的正整数", "error")
                else:
                    for key, value in project_values.items():
                        conn.execute(
                            """INSERT INTO project_settings(project_id,key,value) VALUES (?,?,?)
                              ON CONFLICT(project_id,key) DO UPDATE SET value=excluded.value""",
                            (project_id, key, value),
                        )
                    for key, value in global_values.items():
                        conn.execute(
                            "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, value),
                        )
                    app.config["MAX_CONTENT_LENGTH"] = int(global_values["max_import_mb"]) * 1024 * 1024
                    export_queue = export_queue_provider()
                    if export_queue is not None:
                        export_queue.max_bytes = app.config["MAX_CONTENT_LENGTH"]
                    flash("设置已保存", "success")
                    return redirect(url_for("app_settings"))
            cfg = project_settings(conn, project_id) | settings(conn)
            return render_template(
                "settings.html",
                cfg=cfg,
                project=project,
                modules=project_modules(conn, project_id),
                db_path=str(DB_PATH),
                data_dir=str(DATA_DIR),
                lan_url=lan_url(),
            )
