from __future__ import annotations

import uuid
from pathlib import Path

from flask import Blueprint, abort, flash, redirect, request, send_file, url_for

from services.core.runtime_service import data_dir, db, export_queue
from services.core.common_service import now_iso
from services.core.project_service import current_project
from transfer_service import count_export_questions


bp = Blueprint("exports", __name__)


def export_job_for_browser(conn, job_id):
    project_id = current_project(conn)["id"]
    job = conn.execute("SELECT * FROM export_jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
    if not job:
        abort(404)
    return job

def _export_scope_id() -> int | None:
    value = request.form.get("scope_id", "").strip()
    return int(value) if value.isdigit() else None


def _validate_export_scope(conn, project_id: int, scope_type: str, scope_id: int | None) -> None:
    if scope_type == "project":
        return
    if scope_type == "subject":
        valid = conn.execute("SELECT 1 FROM subjects WHERE id=? AND project_id=?", (scope_id, project_id)).fetchone()
    elif scope_type == "chapter":
        valid = conn.execute("""SELECT 1 FROM chapters c JOIN subjects s ON s.id=c.subject_id
          WHERE c.id=? AND s.project_id=?""", (scope_id, project_id)).fetchone()
    else:
        valid = None
    if not valid:
        raise ValueError("请选择当前项目内的有效导出范围")


@bp.post("/exports")
def export_create():
    with db() as conn:
        project_id = current_project(conn)["id"]
        scope_type = request.form.get("scope_type", "project")
        scope_id = _export_scope_id()
        include_drafts = int("include_drafts" in request.form)
        try:
            _validate_export_scope(conn, project_id, scope_type, scope_id)
            count = count_export_questions(conn, project_id, scope_type, scope_id, bool(include_drafts))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("imports.imports_center", tab="export"))
        if not count:
            flash("当前范围没有可导出的已核验题或草稿题", "error")
            return redirect(url_for("imports.imports_center", tab="export"))
        job_id = uuid.uuid4().hex
        now = now_iso()
        conn.execute("""INSERT INTO export_jobs(id,project_id,scope_type,scope_id,include_drafts,status,stage,
          progress,message,question_count,created_at,updated_at) VALUES (?,?,?,?,?,'queued','queued',0,?,?,?,?)""",
          (job_id, project_id, scope_type, scope_id, include_drafts, "等待生成分享包", count, now, now))
        conn.commit()
        export_queue().submit(job_id)
    flash(f"已创建导出任务，预计包含 {count} 道题", "success")
    return redirect(url_for("imports.imports_center", tab="export"))


@bp.get("/api/exports/count")
def export_count_api():
    with db() as conn:
        project_id = current_project(conn)["id"]
        scope_type = request.args.get("scope_type", "project")
        raw_scope_id = request.args.get("scope_id", "")
        scope_id = int(raw_scope_id) if raw_scope_id.isdigit() else None
        include_drafts = request.args.get("include_drafts") in {"1", "true", "on"}
        try:
            _validate_export_scope(conn, project_id, scope_type, scope_id)
            count = count_export_questions(conn, project_id, scope_type, scope_id, include_drafts)
        except ValueError as exc:
            return {"count": 0, "error": str(exc)}, 400
        return {"count": count}


@bp.get("/api/exports/<job_id>")
def export_status_api(job_id):
    with db() as conn:
        job = export_job_for_browser(conn, job_id)
        return {key: job[key] for key in (
            "id", "status", "stage", "progress", "message", "question_count", "image_count",
            "missing_image_count", "size_bytes", "sha256", "filename", "created_at", "completed_at", "expires_at"
        )}


def _export_file_path(job) -> Path | None:
    if not job["stored_path"]:
        return None
    root = (data_dir() / "exports").resolve()
    path = (data_dir() / job["stored_path"]).resolve()
    return path if path.parent == root else None


@bp.get("/exports/<job_id>/download")
def export_download(job_id):
    with db() as conn:
        job = export_job_for_browser(conn, job_id)
        if job["status"] != "completed" or (job["expires_at"] and job["expires_at"] <= now_iso()):
            abort(404)
        path = _export_file_path(job)
        if not path or not path.is_file():
            abort(404)
        return send_file(path, mimetype="application/zip", as_attachment=True, download_name=job["filename"])


@bp.post("/exports/<job_id>/retry")
def export_retry(job_id):
    with db() as conn:
        job = export_job_for_browser(conn, job_id)
        if job["status"] not in {"failed", "interrupted"}:
            abort(400)
        old_path = _export_file_path(job)
        if old_path:
            old_path.unlink(missing_ok=True)
        conn.execute("""UPDATE export_jobs SET status='queued',stage='queued',progress=0,message='等待重新生成',
          stored_path=NULL,filename=NULL,size_bytes=0,sha256='',warning_json='[]',error_json='[]',
          completed_at=NULL,expires_at=NULL,updated_at=? WHERE id=?""", (now_iso(), job_id))
        conn.commit()
        export_queue().submit(job_id)
    flash("导出任务已重新排队", "success")
    return redirect(url_for("imports.imports_center", tab="export"))


@bp.post("/exports/<job_id>/delete")
def export_delete(job_id):
    with db() as conn:
        job = export_job_for_browser(conn, job_id)
        if job["status"] in {"queued", "running"}:
            flash("正在生成的任务不能删除，请等待完成或中断", "error")
            return redirect(url_for("imports.imports_center", tab="export"))
        path = _export_file_path(job)
        if path:
            path.unlink(missing_ok=True)
        conn.execute("DELETE FROM export_jobs WHERE id=?", (job_id,))
    flash("分享包和任务记录已删除，题库内容不受影响", "success")
    return redirect(url_for("imports.imports_center", tab="export"))
