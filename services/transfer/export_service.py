from __future__ import annotations

import json
import os
import sqlite3
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from services.transfer.transfer_common import (
    IMAGE_SUFFIXES,
    MANIFEST_COMMENT_PREFIX,
    PACKAGE_FORMAT,
    PACKAGE_VERSION,
    _connect,
    _json_bytes,
    _safe_filename,
    _sha256_bytes,
    _sha256_file,
    now_iso,
)


def export_filter(scope_type: str, scope_id: int | None, include_drafts: bool) -> tuple[str, list]:
    if scope_type not in {"project", "subject", "chapter"}:
        raise ValueError("无效的导出范围")
    statuses = ["verified", "draft"] if include_drafts else ["verified"]
    where = ["s.project_id=?", "q.status IN (%s)" % ",".join("?" for _ in statuses)]
    params: list = [None, *statuses]
    if scope_type == "subject":
        if not scope_id:
            raise ValueError("请选择导出科目")
        where.append("s.id=?")
        params.append(scope_id)
    elif scope_type == "chapter":
        if not scope_id:
            raise ValueError("请选择导出章节")
        where.append("c.id=?")
        params.append(scope_id)
    return " AND ".join(where), params


def count_export_questions(
    conn: sqlite3.Connection,
    project_id: int,
    scope_type: str,
    scope_id: int | None,
    include_drafts: bool,
) -> int:
    where, params = export_filter(scope_type, scope_id, include_drafts)
    params[0] = project_id
    return int(conn.execute(f"""SELECT COUNT(*) FROM questions q
      JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
      JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
      WHERE {where}""", params).fetchone()[0])


def _update_export(conn: sqlite3.Connection, job_id: str, **values) -> None:
    values["updated_at"] = now_iso()
    assignments = ",".join(f"{key}=?" for key in values)
    conn.execute(f"UPDATE export_jobs SET {assignments} WHERE id=?", [*values.values(), job_id])
    conn.commit()


def _export_rows(conn: sqlite3.Connection, job) -> list[sqlite3.Row]:
    where, params = export_filter(job["scope_type"], job["scope_id"], bool(job["include_drafts"]))
    params[0] = job["project_id"]
    return conn.execute(f"""SELECT q.type,q.stem,q.options_json,q.answer_json,q.explanation,q.difficulty,
      q.status,q.image_path,kp.id point_id,kp.name point_name,c.id chapter_id,c.name chapter_name,
      c.is_core,s.id subject_id,s.name subject_name,s.code subject_code
      FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
      JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
      WHERE {where} ORDER BY s.id,c.id,kp.id,q.id""", params).fetchall()


def _scope_label(conn: sqlite3.Connection, job) -> str:
    if job["scope_type"] == "project":
        return "全部题目"
    table = "subjects" if job["scope_type"] == "subject" else "chapters"
    row = conn.execute(f"SELECT name FROM {table} WHERE id=?", (job["scope_id"],)).fetchone()
    return row[0] if row else job["scope_type"]


def run_export_job(db_path: Path, data_dir: Path, job_id: str, max_bytes: int) -> None:
    conn = _connect(db_path)
    temp_path: Path | None = None
    try:
        job = conn.execute("SELECT * FROM export_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return
        _update_export(conn, job_id, status="running", stage="collecting", progress=10, message="正在收集题目")
        project = conn.execute("SELECT name,project_type FROM learning_projects WHERE id=?", (job["project_id"],)).fetchone()
        rows = _export_rows(conn, job)
        if not rows:
            raise ValueError("当前范围没有可导出的题目")

        subject_keys: dict[int, str] = {}
        chapter_keys: dict[int, str] = {}
        point_keys: dict[int, str] = {}
        subject_entries: dict[int, dict] = {}
        chapter_entries: dict[int, dict] = {}
        questions: list[dict] = []
        image_payloads: dict[str, bytes] = {}
        image_refs_by_digest: dict[str, str] = {}
        warnings: list[dict] = []
        uploads = (data_dir / "uploads").resolve()

        for index, row in enumerate(rows, 1):
            subject_key = subject_keys.setdefault(row["subject_id"], f"s{len(subject_keys)+1}")
            chapter_key = chapter_keys.setdefault(row["chapter_id"], f"c{len(chapter_keys)+1}")
            point_key = point_keys.setdefault(row["point_id"], f"p{len(point_keys)+1}")
            subject = subject_entries.setdefault(row["subject_id"], {
                "key": subject_key, "name": row["subject_name"], "code": row["subject_code"], "chapters": []
            })
            if row["chapter_id"] not in chapter_entries:
                chapter = {"key": chapter_key, "name": row["chapter_name"], "is_core": bool(row["is_core"]), "points": []}
                chapter_entries[row["chapter_id"]] = chapter
                subject["chapters"].append(chapter)
            chapter = chapter_entries[row["chapter_id"]]
            if not any(item["key"] == point_key for item in chapter["points"]):
                chapter["points"].append({"key": point_key, "name": row["point_name"]})

            image_ref = ""
            image_missing = False
            if row["image_path"]:
                image_path = (uploads / Path(row["image_path"]).name).resolve()
                if image_path.is_file() and image_path.parent == uploads and image_path.suffix.lower() in IMAGE_SUFFIXES:
                    content = image_path.read_bytes()
                    digest = _sha256_bytes(content)
                    image_ref = image_refs_by_digest.get(digest, "")
                    if not image_ref:
                        image_ref = f"images/{digest}{image_path.suffix.lower()}"
                        image_refs_by_digest[digest] = image_ref
                        image_payloads[image_ref] = content
                else:
                    image_missing = True
                    warnings.append({"item": index, "reason": "题目图片文件缺失"})

            questions.append({
                "item_key": f"q{index}", "subject_key": subject_key, "chapter_key": chapter_key,
                "point_key": point_key, "type": row["type"], "stem": row["stem"],
                "options": json.loads(row["options_json"]), "answer": json.loads(row["answer_json"]),
                "explanation": row["explanation"], "difficulty": row["difficulty"], "status": row["status"],
                "image_ref": image_ref, "image_missing": image_missing,
            })

        _update_export(conn, job_id, stage="packing", progress=55, message="正在生成分享包",
                       question_count=len(questions), image_count=len(image_payloads),
                       missing_image_count=sum(1 for item in questions if item["image_missing"]),
                       warning_json=json.dumps(warnings, ensure_ascii=False))

        questions_bytes = _json_bytes(questions)
        files = {"questions.json": _sha256_bytes(questions_bytes)}
        files.update({name: _sha256_bytes(content) for name, content in image_payloads.items()})
        manifest = {
            "format": PACKAGE_FORMAT, "version": PACKAGE_VERSION, "created_at": now_iso(),
            "project": {"name": project["name"], "type": project["project_type"]},
            "scope": {"type": job["scope_type"], "label": _scope_label(conn, job)},
            "include_drafts": bool(job["include_drafts"]),
            "counts": {"questions": len(questions), "images": len(image_payloads),
                       "missing_images": sum(1 for item in questions if item["image_missing"])},
            "subjects": list(subject_entries.values()), "warnings": warnings, "files": files,
        }

        exports = data_dir / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{_safe_filename(project['name'])}-{_safe_filename(_scope_label(conn, job))}-{stamp}.zip"
        temp_path = exports / f".{job_id}.tmp"
        final_path = exports / f"{job_id}_{filename}"
        manifest_bytes = _json_bytes(manifest)
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.comment = MANIFEST_COMMENT_PREFIX + _sha256_bytes(manifest_bytes).encode("ascii")
            archive.writestr("manifest.json", manifest_bytes)
            archive.writestr("questions.json", questions_bytes)
            for name, content in image_payloads.items():
                archive.writestr(name, content)
        size = temp_path.stat().st_size
        if size > max_bytes:
            raise ValueError(f"分享包超过 {max_bytes // 1024 // 1024}MB 上限，请按科目或章节拆分")
        os.replace(temp_path, final_path)
        temp_path = None
        completed = now_iso()
        expires = (datetime.now() + timedelta(days=7)).replace(microsecond=0).isoformat(sep=" ")
        _update_export(conn, job_id, status="completed", stage="complete", progress=100,
                       message="分享包已生成", stored_path=str(Path("exports") / final_path.name),
                       filename=filename, size_bytes=size, sha256=_sha256_file(final_path),
                       completed_at=completed, expires_at=expires)
    except Exception as exc:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        conn.rollback()
        _update_export(conn, job_id, status="failed", stage="failed", progress=0,
                       message=str(exc), error_json=json.dumps([{"reason": str(exc)}], ensure_ascii=False))
    finally:
        conn.close()


def cleanup_expired_exports(db_path: Path, data_dir: Path) -> int:
    conn = _connect(db_path)
    removed = 0
    try:
        rows = conn.execute("SELECT id,stored_path FROM export_jobs WHERE expires_at IS NOT NULL AND expires_at<?", (now_iso(),)).fetchall()
        export_root = (data_dir / "exports").resolve()
        for row in rows:
            if row["stored_path"]:
                path = (data_dir / row["stored_path"]).resolve()
                if path.parent == export_root:
                    path.unlink(missing_ok=True)
            conn.execute("DELETE FROM export_jobs WHERE id=?", (row["id"],))
            removed += 1
        conn.commit()
        return removed
    finally:
        conn.close()


class ExportQueue:
    def __init__(self, db_path: Path, data_dir: Path, max_bytes: int, executor=None):
        self.db_path = db_path
        self.data_dir = data_dir
        self.max_bytes = max_bytes
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="study-export")
        self.lock = threading.Lock()

    def recover(self) -> None:
        conn = _connect(self.db_path)
        try:
            conn.execute("""UPDATE export_jobs SET status='interrupted',stage='interrupted',progress=0,
              message='服务重启导致任务中断，可重新生成',updated_at=? WHERE status IN ('queued','running')""", (now_iso(),))
            conn.commit()
        finally:
            conn.close()
        cleanup_expired_exports(self.db_path, self.data_dir)

    def submit(self, job_id: str) -> None:
        self.executor.submit(run_export_job, self.db_path, self.data_dir, job_id, self.max_bytes)

