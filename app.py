from __future__ import annotations

import hmac
import os
import secrets
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, abort, current_app, flash, redirect, request, session, url_for

from app_runtime import connect_database, configure_file_logging, create_background_executor, load_or_create_secret
from data_management import create_data_management_blueprint
from migrations import LATEST_SCHEMA, PROJECT_SETTING_DEFAULTS, create_project
from services.core.common_service import now_iso
from services.core.migration_service import migrate_with_snapshot
from services.core.project_service import DEFAULT_SETTINGS, current_project, project_modules, settings
from services.core.stats_service import readiness
from services.core.storage_service import (
    APP_PORT,
    backup_data_snapshot as storage_backup_data_snapshot,
    resolve_backup_dir,
    resolve_data_dir,
    resolve_db_path,
)
from services.imports.import_service import ImportQueue
from services.questions.question_service import get_question, record_attempt
from transfer_service import ExportQueue
from version_info import APP_VERSION


SCHEMA = LATEST_SCHEMA
DATA_DIR = resolve_data_dir()
DB_PATH = resolve_db_path(DATA_DIR)
BACKUP_DIR = resolve_backup_dir()

# Compatibility exports used by maintenance scripts and the existing test suite.
__all__ = [
    "APP_PORT",
    "APP_VERSION",
    "BACKUP_DIR",
    "DATA_DIR",
    "DB_PATH",
    "PROJECT_SETTING_DEFAULTS",
    "SCHEMA",
    "app",
    "backup_data_snapshot",
    "background_executor",
    "create_app",
    "create_project",
    "datetime",
    "db",
    "export_queue",
    "get_question",
    "import_queue",
    "now_iso",
    "readiness",
    "record_attempt",
    "timedelta",
]


def _ensure_runtime_dirs(data_root: Path) -> None:
    data_root.mkdir(parents=True, exist_ok=True)
    (data_root / "uploads").mkdir(parents=True, exist_ok=True)


@contextmanager
def _database_context(data_root: Path, database_path: Path):
    _ensure_runtime_dirs(data_root)
    connection = connect_database(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def db():
    """Compatibility database provider for scripts and the existing test suite."""
    return _database_context(DATA_DIR, DB_PATH)


def backup_data_snapshot(conn, label):
    """Compatibility backup helper for scripts importing the application module."""
    return storage_backup_data_snapshot(conn, label, data_dir=DATA_DIR, db_path=DB_PATH, backup_dir=BACKUP_DIR)


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def protect_state_changing_requests():
    if current_app.config.get("TESTING") or request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("_csrf_token")
    expected = session.get("csrf_token")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        abort(400, description="页面安全令牌已失效，请刷新后重试")
    return None


def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if response.status_code >= 500:
        current_app.logger.error("%s %s -> %s", request.method, request.path, response.status_code)
    return response


def upload_too_large(_error):
    flash("上传文件超过当前导入上限，请在设置中调整或拆分文件", "error")
    return redirect(url_for("imports.imports_center"))


def _template_globals(db_provider):
    with db_provider() as conn:
        project = current_project(conn)
        projects = conn.execute(
            "SELECT * FROM learning_projects WHERE status='active' ORDER BY name,id"
        ).fetchall()
        modules = project_modules(conn, project["id"])
        global_due = conn.execute(
            """SELECT COUNT(*) n FROM question_progress p JOIN questions q ON q.id=p.question_id
              JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
              JOIN subjects s ON s.id=c.subject_id WHERE q.status='verified' AND p.due_date<=?""",
            (date.today().isoformat(),),
        ).fetchone()["n"]
    return {
        "TYPE_NAMES": {"single": "单选", "multiple": "多选", "true_false": "判断", "fill": "填空", "short": "简答"},
        "STATUS_NAMES": {"draft": "草稿", "verified": "已核验", "archived": "已归档"},
        "PROJECT_TYPE_NAMES": {
            "practice": "普通刷题",
            "exam_prep": "考试备考",
            "practical_certification": "实操认证",
        },
        "LETTERS": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "today": date.today().isoformat(),
        "APP_VERSION": current_app.config["APP_VERSION"],
        "BUILD_COMMIT": current_app.config["BUILD_COMMIT"],
        "current_project": project,
        "available_projects": projects,
        "current_modules": modules,
        "global_due_count": global_due,
        "csrf_token": csrf_token(),
    }


def _initialize_database(app: Flask, db_provider) -> None:
    with db_provider() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        app.extensions["study_migration"] = migrate_with_snapshot(
            conn,
            data_dir=Path(app.config["STUDY_DATA_DIR"]),
            db_path=Path(app.config["STUDY_DB_PATH"]),
            backup_dir=Path(app.config["STUDY_BACKUP_DIR"]),
            logger=app.logger,
        )
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (key, value))
        configured_limit = os.environ.get("STUDY_MAX_UPLOAD_MB") or settings(conn).get("max_import_mb", "50")
        app.config["MAX_CONTENT_LENGTH"] = int(configured_limit) * 1024 * 1024


def _register_routes(app: Flask, db_provider, backup_provider) -> None:
    from routes.dashboard import register_dashboard_routes
    from routes.exports import bp as exports_blueprint
    from routes.imports import bp as imports_blueprint
    from routes.knowledge import bp as knowledge_blueprint
    from routes.labs import bp as labs_blueprint
    from routes.mock import bp as mock_blueprint
    from routes.onboarding import bp as onboarding_blueprint, require_onboarding
    from routes.plans import bp as plans_blueprint
    from routes.practice import bp as practice_blueprint
    from routes.projects import bp as projects_blueprint
    from routes.questions import bp as questions_blueprint
    from routes.settings import register_settings_routes
    from routes.uploads import register_upload_routes

    register_dashboard_routes(app, db_provider)
    app.before_request(require_onboarding)
    register_settings_routes(app, db_provider, lambda: app.extensions.get("study_export_queue"))
    app.register_blueprint(projects_blueprint)
    app.register_blueprint(questions_blueprint)
    app.register_blueprint(imports_blueprint)
    app.register_blueprint(exports_blueprint)
    app.register_blueprint(knowledge_blueprint)
    app.register_blueprint(practice_blueprint)
    app.register_blueprint(mock_blueprint)
    app.register_blueprint(onboarding_blueprint)
    app.register_blueprint(labs_blueprint)
    app.register_blueprint(plans_blueprint)
    register_upload_routes(app, db_provider)
    app.register_blueprint(
        create_data_management_blueprint(
            db_provider=db_provider,
            current_project_fn=current_project,
            backup_fn=backup_provider,
            backup_dir_fn=lambda: Path(app.config["STUDY_BACKUP_DIR"]),
            db_path_fn=lambda: Path(app.config["STUDY_DB_PATH"]),
        )
    )


def create_app(config: dict | None = None) -> Flask:
    """Create an isolated Flask application while preserving the legacy module entry point."""
    overrides = dict(config or {})
    data_root = Path(overrides.get("STUDY_DATA_DIR") or resolve_data_dir()).resolve()
    database_path = Path(overrides.get("STUDY_DB_PATH") or resolve_db_path(data_root)).resolve()
    backups = Path(overrides.get("STUDY_BACKUP_DIR") or resolve_backup_dir()).resolve()

    application = Flask(__name__)
    application.config.update(
        SECRET_KEY=overrides.get("SECRET_KEY") or load_or_create_secret(data_root),
        MAX_CONTENT_LENGTH=int(os.environ.get("STUDY_MAX_UPLOAD_MB", "50")) * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        APP_VERSION=os.environ.get("STUDY_APP_VERSION", APP_VERSION),
        BUILD_COMMIT=os.environ.get("STUDY_BUILD_COMMIT", "development"),
        STUDY_DATA_DIR=data_root,
        STUDY_DB_PATH=database_path,
        STUDY_BACKUP_DIR=backups,
    )
    application.config.update(overrides)
    configure_file_logging(application.logger, data_root)

    def app_db():
        return _database_context(data_root, database_path)

    def app_backup(conn, label):
        return storage_backup_data_snapshot(
            conn,
            label,
            data_dir=data_root,
            db_path=database_path,
            backup_dir=backups,
        )

    application.extensions["study_db"] = app_db
    application.extensions["study_backup_snapshot"] = app_backup
    application.before_request(protect_state_changing_requests)
    application.after_request(add_security_headers)
    application.register_error_handler(413, upload_too_large)
    application.context_processor(lambda: _template_globals(app_db))

    _initialize_database(application, app_db)
    _register_routes(application, app_db, app_backup)

    executor = create_background_executor()
    import_worker = ImportQueue(database_path, data_root, executor=executor)
    export_worker = ExportQueue(database_path, data_root, application.config["MAX_CONTENT_LENGTH"], executor=executor)
    application.extensions["study_background_executor"] = executor
    application.extensions["study_import_queue"] = import_worker
    application.extensions["study_export_queue"] = export_worker
    import_worker.recover()
    export_worker.recover()
    return application


app = create_app()
background_executor = app.extensions["study_background_executor"]
import_queue = app.extensions["study_import_queue"]
export_queue = app.extensions["study_export_queue"]


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
