from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from io import BytesIO
from pathlib import Path

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename

from services.core.runtime_service import data_dir, db, db_path, import_queue
from services.core.common_service import now_iso
from services.imports.import_service import PARSER_VERSION, SUPPORTED_TYPES, commit_job
from migrations import PROJECT_MODULE_DEFAULTS, create_project
from services.core.project_service import current_project, settings
from transfer_service import cleanup_expired_exports


bp = Blueprint("imports", __name__)


def import_job_for_browser(conn, job_id):
    job = conn.execute("""SELECT j.*,d.original_name,d.stored_path,d.sha256,d.file_type,d.size_bytes
      FROM import_jobs j JOIN source_documents d ON d.id=j.source_document_id WHERE j.id=?""", (job_id,)).fetchone()
    if not job:
        abort(404)
    allowed = set(session.get("import_job_ids", []))
    current_id = current_project(conn)["id"]
    if job["project_id"] not in {None, current_id} and job_id not in allowed:
        abort(404)
    return job

def package_mapping_suggestions(conn, project_id, package):
    targets = conn.execute("SELECT id,name,code FROM subjects WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
    mappings, conflicts = {}, []
    for source in package.get("subjects", []):
        code = (source.get("code") or "").strip().casefold()
        name = (source.get("name") or "").strip().casefold()
        by_code = next((row for row in targets if code and (row["code"] or "").strip().casefold() == code), None)
        by_name = next((row for row in targets if (row["name"] or "").strip().casefold() == name), None)
        if by_code and by_name and by_code["id"] != by_name["id"]:
            conflicts.append({"key": source["key"], "name": source["name"], "code": source.get("code", ""),
                              "code_target": by_code["id"], "name_target": by_name["id"]})
            target_id = None
        else:
            target_id = (by_code or by_name)["id"] if (by_code or by_name) else None
        mappings[source["key"]] = {"target_subject_id": target_id, "name": source["name"], "code": source.get("code", "")}
    return {"subjects": mappings, "conflicts": conflicts}


@bp.route("/imports", methods=["GET", "POST"])
def imports_center():
    cleanup_expired_exports(db_path(), data_dir())
    with db() as conn:
        project = current_project(conn); project_id = project["id"]
        if request.method == "POST":
            uploaded = request.files.get("file")
            if not uploaded or not uploaded.filename:
                flash("请选择要导入的文件", "error")
                return redirect(url_for("imports.imports_center"))
            max_bytes = int(settings(conn).get("max_import_mb", "50")) * 1024 * 1024
            uploaded.stream.seek(0, 2); size = uploaded.stream.tell(); uploaded.stream.seek(0)
            if size > max_bytes:
                flash(f"文件超过 {max_bytes // 1024 // 1024}MB 上限", "error")
                return redirect(url_for("imports.imports_center"))
            original_name = Path(uploaded.filename).name
            ext = Path(original_name).suffix.lower().lstrip(".")
            document_id = uuid.uuid4().hex
            job_id = uuid.uuid4().hex
            originals = data_dir() / "imports" / "originals"
            originals.mkdir(parents=True, exist_ok=True)
            safe_name = secure_filename(original_name) or f"document.{ext or 'bin'}"
            stored_relative = Path("imports") / "originals" / f"{document_id}_{safe_name}"
            stored_path = data_dir() / stored_relative
            uploaded.save(stored_path)
            digest = hashlib.sha256(stored_path.read_bytes()).hexdigest()
            status = "queued" if ext in SUPPORTED_TYPES else "failed"
            message = "等待文件探测" if status == "queued" else "不支持该格式；仅支持 ZIP、PDF、DOCX、XLSX、CSV，不支持 DOC、XLS 和未知版式"
            now = now_iso()
            conn.execute("""INSERT INTO source_documents(id,original_name,stored_path,sha256,file_type,size_bytes,created_at)
              VALUES (?,?,?,?,?,?,?)""", (document_id, original_name, str(stored_relative), digest, ext or "unknown", size, now))
            conn.execute("""INSERT INTO import_jobs(id,source_document_id,status,stage,progress,message,parser_version,
              error_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
              (job_id, document_id, status, "detecting" if status == "queued" else "rejected", 0, message,
               PARSER_VERSION, "[]" if status == "queued" else json.dumps([{"reason": message}], ensure_ascii=False), now, now))
            jobs = list(dict.fromkeys([job_id, *session.get("import_job_ids", [])]))[:20]
            session["import_job_ids"] = jobs
            conn.commit()
            if status == "queued":
                import_queue().submit_detect(job_id)
            flash("原文件已保存，正在探测格式" if status == "queued" else message, "success" if status == "queued" else "error")
            return redirect(url_for("imports.import_detail", job_id=job_id))
        personal_ids = session.get("import_job_ids", [])
        params = [project_id]
        where = "j.project_id=?"
        if personal_ids:
            where += " OR j.id IN (%s)" % ",".join("?" for _ in personal_ids)
            params.extend(personal_ids)
        rows = conn.execute(f"""SELECT j.*,d.original_name,d.file_type FROM import_jobs j JOIN source_documents d
          ON d.id=j.source_document_id WHERE {where} ORDER BY j.created_at DESC LIMIT 50""", params).fetchall()
        export_rows = conn.execute("SELECT * FROM export_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 50", (project_id,)).fetchall()
        subjects = conn.execute("SELECT id,name,code FROM subjects WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        chapters = conn.execute("""SELECT c.id,c.name,c.subject_id,s.name subject_name FROM chapters c
          JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY s.id,c.id""", (project_id,)).fetchall()
        return render_template("imports.html", jobs=rows, exports=export_rows, cfg=settings(conn),
                               subjects=subjects, chapters=chapters, project=project,
                               has_active_exports=any(row["status"] in {"queued", "running"} for row in export_rows))


@bp.get("/imports/<job_id>")
def import_detail(job_id):
    with db() as conn:
        job = import_job_for_browser(conn, job_id)
        project_rows = conn.execute("SELECT * FROM learning_projects WHERE status='active' ORDER BY name").fetchall()
        subject_rows = conn.execute("SELECT id,project_id,name,code FROM subjects ORDER BY project_id,name").fetchall()
        candidates = conn.execute("SELECT * FROM import_candidates WHERE job_id=? ORDER BY item_index LIMIT 200", (job_id,)).fetchall()
        return render_template("import_detail.html", job=job, projects=project_rows, subjects=subject_rows,
                               candidates=candidates, detected=json.loads(job["detected_json"] or "{}"),
                               package=json.loads(job["package_json"] or "{}"),
                               mapping=json.loads(job["mapping_json"] or "{}"),
                               errors=json.loads(job["error_json"] or "[]"))


@bp.get("/api/imports/<job_id>")
def import_status_api(job_id):
    with db() as conn:
        job = import_job_for_browser(conn, job_id)
        return {key: job[key] for key in ("id", "status", "stage", "progress", "message", "candidate_count",
          "valid_count", "duplicate_count", "committed_count", "updated_at", "completed_at")}


@bp.post("/imports/<job_id>/target")
def import_target(job_id):
    with db() as conn:
        job = import_job_for_browser(conn, job_id)
        detected = json.loads(job["detected_json"] or "{}")
        new_project_name = request.form.get("new_project_name", "").strip()
        if job["import_kind"] == "package" and new_project_name:
            package_type = detected.get("package", {}).get("project", {}).get("type", "practice")
            if package_type not in PROJECT_MODULE_DEFAULTS:
                package_type = "practice"
            project_id = create_project(conn, new_project_name, package_type)
        else:
            project_id = request.form.get("project_id", type=int)
        project = conn.execute("SELECT * FROM learning_projects WHERE id=? AND status='active'", (project_id,)).fetchone()
        if not project:
            abort(400)
        if job["import_kind"] == "package":
            package = detected.get("package", {})
            mapping = package_mapping_suggestions(conn, project_id, package)
            now = now_iso()
            conn.execute("UPDATE source_documents SET project_id=?,subject_id=NULL WHERE id=?", (project_id, job["source_document_id"]))
            conn.execute("""UPDATE import_jobs SET project_id=?,subject_id=NULL,status='waiting_mapping',stage='mapping',
              progress=25,message='请选择科目映射',mapping_json=?,updated_at=? WHERE id=?""",
              (project_id, json.dumps(mapping, ensure_ascii=False), now, job_id))
            conn.commit()
            session["current_project_id"] = project_id
            flash("目标项目已确认，请检查科目映射", "success")
            return redirect(url_for("imports.import_detail", job_id=job_id))
        subject_id = request.form.get("subject_id", type=int)
        if request.form.get("new_subject_name", "").strip():
            try:
                subject_id = conn.execute("INSERT INTO subjects(project_id,name,code) VALUES (?,?,?)",
                    (project_id, request.form["new_subject_name"].strip(), request.form.get("new_subject_code", "").strip())).lastrowid
            except sqlite3.IntegrityError:
                flash("同一项目内科目代码不能重复", "error")
                return redirect(url_for("imports.import_detail", job_id=job_id))
        subject = conn.execute("SELECT * FROM subjects WHERE id=? AND project_id=?", (subject_id, project_id)).fetchone()
        if not subject:
            flash("请选择已有科目，或填写新科目名称", "error")
            return redirect(url_for("imports.import_detail", job_id=job_id))
        now = now_iso()
        conn.execute("UPDATE source_documents SET project_id=?,subject_id=? WHERE id=?", (project_id, subject_id, job["source_document_id"]))
        conn.execute("""UPDATE import_jobs SET project_id=?,subject_id=?,status='queued',stage='parsing',progress=25,
          message='等待解析',updated_at=? WHERE id=?""", (project_id, subject_id, now, job_id))
        conn.commit()
        session["current_project_id"] = project_id
        import_queue().submit_parse(job_id)
    flash("目标科目已确认，正在完整解析", "success")
    return redirect(url_for("imports.import_detail", job_id=job_id))


@bp.post("/imports/<job_id>/mapping")
def import_mapping(job_id):
    with db() as conn:
        job = import_job_for_browser(conn, job_id)
        if job["import_kind"] != "package" or job["status"] != "waiting_mapping" or not job["project_id"]:
            abort(400)
        detected = json.loads(job["detected_json"] or "{}")
        suggested = json.loads(job["mapping_json"] or "{}").get("subjects", {})
        result, used = {}, set()
        for source in detected.get("package", {}).get("subjects", []):
            raw = request.form.get(f"map_{source['key']}", "")
            if not raw:
                value = suggested.get(source["key"], {}).get("target_subject_id")
            elif raw == "new":
                value = None
            elif raw.isdigit():
                value = int(raw)
            else:
                flash("科目映射格式无效", "error")
                return redirect(url_for("imports.import_detail", job_id=job_id))
            if value:
                if value in used or not conn.execute(
                    "SELECT 1 FROM subjects WHERE id=? AND project_id=?", (value, job["project_id"])
                ).fetchone():
                    flash("不同来源科目不能映射到同一个目标科目", "error")
                    return redirect(url_for("imports.import_detail", job_id=job_id))
                used.add(value)
            result[source["key"]] = {"target_subject_id": value, "name": source["name"], "code": source.get("code", "")}
        conn.execute("""UPDATE import_jobs SET mapping_json=?,status='queued',stage='parsing',progress=30,
          message='等待解析分享包',updated_at=? WHERE id=?""",
          (json.dumps({"subjects": result}, ensure_ascii=False), now_iso(), job_id))
        conn.commit()
        import_queue().submit_parse(job_id)
    flash("科目映射已确认，正在解析分享包", "success")
    return redirect(url_for("imports.import_detail", job_id=job_id))


@bp.post("/imports/<job_id>/retry")
def import_retry(job_id):
    with db() as conn:
        job = import_job_for_browser(conn, job_id)
        package_ready = job["import_kind"] == "package" and bool(json.loads(job["mapping_json"] or "{}").get("subjects"))
        conn.execute("UPDATE import_jobs SET status='queued',progress=?,message='等待重试',updated_at=? WHERE id=?",
                     (25 if job["subject_id"] or package_ready else 0, now_iso(), job_id))
        conn.commit()
        if job["subject_id"] or package_ready:
            import_queue().submit_parse(job_id)
        else:
            import_queue().submit_detect(job_id)
    flash("已从保存的原文件重新排队", "success")
    return redirect(url_for("imports.import_detail", job_id=job_id))


@bp.post("/imports/<job_id>/commit")
def import_commit(job_id):
    with db() as conn:
        import_job_for_browser(conn, job_id)
        candidates = conn.execute("SELECT * FROM import_candidates WHERE job_id=? ORDER BY item_index", (job_id,)).fetchall()
        decisions = {candidate["id"]: request.form.get(f"decision_{candidate['id']}") for candidate in candidates
                     if request.form.get(f"decision_{candidate['id']}")}
        try:
            result = commit_job(
                conn,
                job_id,
                decisions,
                request.form.get("duplicate_action", "skip"),
                request.form.get("status_strategy", "draft"),
                data_dir(),
            )
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), "error")
            return redirect(url_for("imports.import_detail", job_id=job_id))
    flash(f"导入完成：{result['message']}", "success")
    return redirect(url_for("imports.import_detail", job_id=job_id))



@bp.get("/imports/template/<file_type>")
def import_template(file_type):
    headers = ["题型", "题干", *[f"选项{x}" for x in "ABCDEFGH"], "答案", "解析", "章节", "知识点", "原题号"]
    example = ["单选", "示例题干", "选项一", "选项二", "", "", "", "", "", "", "A", "示例解析", "示例章节", "示例知识点", "1"]
    if file_type == "csv":
        content = ",".join(headers) + "\r\n" + ",".join(example) + "\r\n"
        return send_file(BytesIO(content.encode("utf-8-sig")), mimetype="text/csv", as_attachment=True, download_name="I-Love-Learning题库导入模板.csv")
    if file_type == "xlsx":
        from openpyxl import Workbook
        workbook = Workbook(); sheet = workbook.active; sheet.title = "题库"
        sheet.append(headers); sheet.append(example); stream = BytesIO(); workbook.save(stream); stream.seek(0)
        return send_file(stream, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name="I-Love-Learning题库导入模板.xlsx")
    abort(404)
