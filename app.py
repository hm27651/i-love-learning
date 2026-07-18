from __future__ import annotations

import os
import secrets
import hashlib
import hmac
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from flask import Flask, abort, flash, redirect, request, session, url_for

from migrations import LATEST_SCHEMA, PROJECT_SETTING_DEFAULTS, create_project, migrate_database
from services.imports.import_service import ImportQueue
from transfer_service import ExportQueue
from data_management import create_data_management_blueprint
from app_runtime import connect_database, configure_file_logging, create_background_executor, load_or_create_secret
from services.core.common_service import now_iso
from services.core.project_service import (
    DEFAULT_SETTINGS,
    current_project,
    project_modules,
    settings,
)
from services.core.stats_service import readiness
from services.questions.question_service import get_question, record_attempt
from version_info import APP_VERSION
from services.core.storage_service import (
    APP_PORT,
    BACKUP_DIR,
    DATA_DIR,
    DB_PATH,
    ensure_data_dirs,
    backup_data_snapshot as storage_backup_data_snapshot,
)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=load_or_create_secret(DATA_DIR),
    MAX_CONTENT_LENGTH=int(os.environ.get("STUDY_MAX_UPLOAD_MB", "50")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    APP_VERSION=os.environ.get("STUDY_APP_VERSION", APP_VERSION),
    BUILD_COMMIT=os.environ.get("STUDY_BUILD_COMMIT", "development"),
)
configure_file_logging(app.logger, DATA_DIR)


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.before_request
def protect_state_changing_requests():
    if app.config.get("TESTING") or request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("_csrf_token")
    expected = session.get("csrf_token")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        abort(400, description="页面安全令牌已失效，请刷新后重试")
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if response.status_code >= 500:
        app.logger.error("%s %s -> %s", request.method, request.path, response.status_code)
    return response


@app.errorhandler(413)
def upload_too_large(_error):
    flash("上传文件超过当前导入上限，请在设置中调整或拆分文件", "error")
    return redirect(url_for("imports.imports_center"))


SCHEMA = LATEST_SCHEMA

@contextmanager
def db():
    ensure_data_dirs()
    connection = connect_database(DB_PATH)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    with db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        migrate_database(conn)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (key, value))


def backup_data_snapshot(conn, label):
    return storage_backup_data_snapshot(conn, label, data_dir=DATA_DIR, db_path=DB_PATH, backup_dir=BACKUP_DIR)



@app.context_processor
def template_globals():
    with db() as conn:
        project = current_project(conn)
        projects = conn.execute("SELECT * FROM learning_projects WHERE status='active' ORDER BY name,id").fetchall()
        modules = project_modules(conn, project["id"])
        global_due = conn.execute("""SELECT COUNT(*) n FROM question_progress p JOIN questions q ON q.id=p.question_id
          JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
          JOIN subjects s ON s.id=c.subject_id WHERE q.status='verified' AND p.due_date<=?""",
          (date.today().isoformat(),)).fetchone()["n"]
    return {"TYPE_NAMES": {"single": "单选", "multiple": "多选", "true_false": "判断", "fill": "填空", "short": "简答"},
            "STATUS_NAMES": {"draft": "草稿", "verified": "已核验", "archived": "已归档"},
            "PROJECT_TYPE_NAMES": {"practice": "普通刷题", "exam_prep": "考试备考", "practical_certification": "实操认证"},
            "LETTERS": "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "today": date.today().isoformat(),
            "APP_VERSION": app.config["APP_VERSION"], "BUILD_COMMIT": app.config["BUILD_COMMIT"],
            "current_project": project, "available_projects": projects, "current_modules": modules,
            "global_due_count": global_due, "csrf_token": csrf_token()}




from routes.projects import bp as projects_blueprint
from routes.questions import bp as questions_blueprint
from routes.imports import bp as imports_blueprint
from routes.exports import bp as exports_blueprint
from routes.knowledge import bp as knowledge_blueprint
from routes.practice import bp as practice_blueprint
from routes.mock import bp as mock_blueprint
from routes.labs import bp as labs_blueprint
from routes.plans import bp as plans_blueprint
from routes.dashboard import register_dashboard_routes
from routes.settings import register_settings_routes
from routes.uploads import register_upload_routes


register_dashboard_routes(app, db)
register_settings_routes(app, db, lambda: globals().get("export_queue"))
app.register_blueprint(projects_blueprint)
app.register_blueprint(questions_blueprint)
app.register_blueprint(imports_blueprint)
app.register_blueprint(exports_blueprint)
app.register_blueprint(knowledge_blueprint)
app.register_blueprint(practice_blueprint)
app.register_blueprint(mock_blueprint)
app.register_blueprint(labs_blueprint)
app.register_blueprint(plans_blueprint)
register_upload_routes(app, db)
app.register_blueprint(create_data_management_blueprint(
    db_provider=db,
    current_project_fn=current_project,
    backup_fn=backup_data_snapshot,
    backup_dir_fn=lambda: BACKUP_DIR,
    db_path_fn=lambda: DB_PATH,
))


init_db()
with db() as _startup_conn:
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("STUDY_MAX_UPLOAD_MB") or settings(_startup_conn).get("max_import_mb", "50")) * 1024 * 1024
background_executor = create_background_executor()
import_queue = ImportQueue(DB_PATH, DATA_DIR, executor=background_executor)
import_queue.recover()
export_queue = ExportQueue(DB_PATH, DATA_DIR, app.config["MAX_CONTENT_LENGTH"], executor=background_executor)
export_queue.recover()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
