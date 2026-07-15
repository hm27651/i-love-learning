from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath


PACKAGE_FORMAT = "i-love-learning-question-bank"
PACKAGE_VERSION = 1
MANIFEST_COMMENT_PREFIX = b"ILL-MANIFEST-SHA256:"
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_PACKAGE_FILES = 5000
MAX_COMPRESSION_RATIO = 100
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
QUESTION_TYPES = {"single", "multiple", "true_false", "fill", "short"}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _safe_filename(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "-", value).strip(" .-")
    return value[:80] or "题库"


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
    def __init__(self, db_path: Path, data_dir: Path, max_bytes: int):
        self.db_path = db_path
        self.data_dir = data_dir
        self.max_bytes = max_bytes
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="study-export")
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


def _validate_zip_path(name: str) -> None:
    if "\\" in name:
        raise ValueError("ZIP 包含非法路径分隔符")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("ZIP 包含不安全路径")


def _validate_question(
    record: dict,
    subject_keys: set[str],
    chapter_subjects: dict[str, str],
    point_chapters: dict[str, str],
    files: set[str],
) -> None:
    required = {"item_key", "subject_key", "chapter_key", "point_key", "type", "stem", "options", "answer",
                "explanation", "difficulty", "status", "image_ref", "image_missing"}
    if set(record) != required:
        raise ValueError("题目字段与分享包格式不一致")
    if record["type"] not in QUESTION_TYPES or record["status"] not in {"verified", "draft"}:
        raise ValueError("题型或审核状态无效")
    if not isinstance(record["stem"], str) or not record["stem"].strip():
        raise ValueError("分享包存在空题干")
    if (not isinstance(record["options"], list) or not all(isinstance(value, str) for value in record["options"])
            or not isinstance(record["answer"], list) or not record["answer"]
            or not all(isinstance(value, str) for value in record["answer"])):
        raise ValueError("分享包存在无效答案")
    if not isinstance(record["explanation"], str):
        raise ValueError("题目解析格式无效")
    if (record["subject_key"] not in subject_keys
            or chapter_subjects.get(record["chapter_key"]) != record["subject_key"]
            or point_chapters.get(record["point_key"]) != record["chapter_key"]):
        raise ValueError("题目引用了不存在的知识结构")
    if record["type"] in {"single", "multiple"}:
        labels = set("ABCDEFGH"[:len(record["options"])])
        if len(record["options"]) < 2 or not set(record["answer"]).issubset(labels):
            raise ValueError("客观题答案与选项不一致")
    if isinstance(record["difficulty"], bool) or not isinstance(record["difficulty"], int) or record["difficulty"] not in {1, 2, 3}:
        raise ValueError("题目难度无效")
    if not isinstance(record["image_missing"], bool):
        raise ValueError("题目图片状态无效")
    image_ref = record["image_ref"]
    if not isinstance(image_ref, str) or (record["image_missing"] and image_ref):
        raise ValueError("题目图片引用无效")
    if image_ref and image_ref not in files:
        raise ValueError("题目引用的图片不存在")


def inspect_share_package(path: Path, extract_dir: Path | None = None) -> tuple[dict, list[dict]]:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIP 文件损坏或格式无效") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_PACKAGE_FILES:
            raise ValueError("ZIP 文件数量超过 5000 个")
        names: set[str] = set()
        expanded = 0
        for info in infos:
            _validate_zip_path(info.filename)
            if info.is_dir():
                raise ValueError("ZIP 不允许包含目录项")
            if info.filename in names:
                raise ValueError("ZIP 包含重复路径")
            names.add(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("ZIP 不允许包含符号链接")
            expanded += info.file_size
            if expanded > MAX_EXPANDED_BYTES:
                raise ValueError("ZIP 解压后超过 200MB 上限")
            if info.file_size and (not info.compress_size or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO):
                raise ValueError("ZIP 包含异常压缩率文件")
        if "manifest.json" not in names or "questions.json" not in names:
            raise ValueError("ZIP 缺少 manifest.json 或 questions.json")
        try:
            manifest_bytes = archive.read("manifest.json")
            questions_bytes = archive.read("questions.json")
            expected_comment = MANIFEST_COMMENT_PREFIX + _sha256_bytes(manifest_bytes).encode("ascii")
            if archive.comment != expected_comment:
                raise ValueError("manifest.json 完整性校验失败，分享包可能已被修改")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            questions = json.loads(questions_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
            raise ValueError("分享包 JSON 无法解析") from exc
        if manifest.get("format") != PACKAGE_FORMAT or manifest.get("version") != PACKAGE_VERSION:
            raise ValueError("不支持的分享包格式或版本")
        required_manifest = {"format", "version", "created_at", "project", "scope", "include_drafts",
                             "counts", "subjects", "warnings", "files"}
        if set(manifest) != required_manifest:
            raise ValueError("manifest.json 字段与分享包格式不一致")
        project = manifest.get("project")
        scope = manifest.get("scope")
        counts = manifest.get("counts")
        if (not isinstance(project, dict) or set(project) != {"name", "type"}
                or not isinstance(project["name"], str) or not project["name"].strip()
                or not isinstance(project["type"], str)):
            raise ValueError("分享包项目建议无效")
        if (not isinstance(scope, dict) or set(scope) != {"type", "label"}
                or scope["type"] not in {"project", "subject", "chapter"}
                or not isinstance(scope["label"], str)):
            raise ValueError("分享包范围无效")
        if (not isinstance(counts, dict) or set(counts) != {"questions", "images", "missing_images"}
                or any(isinstance(counts[key], bool) or not isinstance(counts[key], int) or counts[key] < 0 for key in counts)):
            raise ValueError("分享包数量统计无效")
        if not isinstance(manifest.get("include_drafts"), bool) or not isinstance(manifest.get("warnings"), list):
            raise ValueError("分享包设置或警告格式无效")
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != names - {"manifest.json"}:
            raise ValueError("分享包文件清单不一致")
        allowed_files = {"questions.json"}
        for name in files:
            path = PurePosixPath(name)
            if name != "questions.json":
                if len(path.parts) != 2 or path.parts[0] != "images" or path.suffix.lower() not in IMAGE_SUFFIXES:
                    raise ValueError(f"分享包包含未知文件：{name}")
                allowed_files.add(name)
        if set(files) != allowed_files:
            raise ValueError("分享包文件清单无效")
        for name, expected in files.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or _sha256_bytes(archive.read(name)) != expected:
                raise ValueError(f"文件校验失败：{name}")
            if name.startswith("images/") and PurePosixPath(name).stem != expected:
                raise ValueError(f"图片内容哈希与文件名不一致：{name}")
        if not isinstance(questions, list) or manifest.get("counts", {}).get("questions") != len(questions):
            raise ValueError("分享包题目数量不一致")
        if counts["images"] != len(files) - 1:
            raise ValueError("分享包图片数量不一致")
        subjects = manifest.get("subjects")
        if not isinstance(subjects, list) or not subjects:
            raise ValueError("分享包缺少科目结构")
        subject_keys: set[str] = set()
        chapter_keys: set[str] = set()
        point_keys: set[str] = set()
        chapter_subjects: dict[str, str] = {}
        point_chapters: dict[str, str] = {}
        for subject in subjects:
            if (not isinstance(subject, dict) or set(subject) != {"key", "name", "code", "chapters"}
                    or not isinstance(subject["key"], str) or not subject["key"]
                    or not isinstance(subject["name"], str) or not subject["name"].strip()
                    or not isinstance(subject["code"], str) or not isinstance(subject["chapters"], list)):
                raise ValueError("科目结构无效")
            if subject["key"] in subject_keys:
                raise ValueError("科目键重复")
            subject_keys.add(subject["key"])
            for chapter in subject["chapters"]:
                if (not isinstance(chapter, dict) or set(chapter) != {"key", "name", "is_core", "points"}
                        or not isinstance(chapter["key"], str) or not chapter["key"]
                        or not isinstance(chapter["name"], str) or not chapter["name"].strip()
                        or not isinstance(chapter["is_core"], bool) or not isinstance(chapter["points"], list)):
                    raise ValueError("章节结构无效")
                if chapter["key"] in chapter_keys:
                    raise ValueError("章节键重复")
                chapter_keys.add(chapter["key"])
                chapter_subjects[chapter["key"]] = subject["key"]
                for point in chapter["points"]:
                    if (not isinstance(point, dict) or set(point) != {"key", "name"}
                            or not isinstance(point["key"], str) or not point["key"]
                            or not isinstance(point["name"], str) or not point["name"].strip()
                            or point["key"] in point_keys):
                        raise ValueError("知识点结构无效")
                    point_keys.add(point["key"])
                    point_chapters[point["key"]] = chapter["key"]
        item_keys: set[str] = set()
        referenced_images: set[str] = set()
        for record in questions:
            if not isinstance(record, dict):
                raise ValueError("题目记录格式无效")
            _validate_question(record, subject_keys, chapter_subjects, point_chapters, set(files))
            if not isinstance(record["item_key"], str) or not record["item_key"] or record["item_key"] in item_keys:
                raise ValueError("题目标识重复")
            item_keys.add(record["item_key"])
            if record["image_ref"]:
                referenced_images.add(record["image_ref"])
        if referenced_images != set(files) - {"questions.json"}:
            raise ValueError("分享包包含未引用图片或缺少图片引用")
        if counts["missing_images"] != sum(1 for record in questions if record["image_missing"]):
            raise ValueError("分享包缺图数量不一致")
        if extract_dir:
            extract_dir.mkdir(parents=True, exist_ok=True)
            for name in files:
                if name.startswith("images/"):
                    target = extract_dir / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
        return manifest, questions
