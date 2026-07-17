"""Command-line entry point for the same import pipeline used by the web UI."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from import_service import PARSER_VERSION, SUPPORTED_TYPES, commit_job, now_iso, parse_job
from migrations import migrate_database


def main() -> int:
    parser = argparse.ArgumentParser(description="Import one structured question-bank file")
    parser.add_argument("file", type=Path)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "h3cse.db")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--project-id", required=True, type=int)
    parser.add_argument("--subject-id", required=True, type=int)
    parser.add_argument("--duplicate-action", choices=("skip", "update", "copy"), default="skip")
    parser.add_argument("--commit", action="store_true", help="Write parsed candidates as draft questions")
    args = parser.parse_args()

    source = args.file.resolve()
    if not source.is_file():
        raise SystemExit(f"File not found: {source}")
    file_type = source.suffix.lower().lstrip(".")
    if file_type not in SUPPORTED_TYPES:
        raise SystemExit("Only PDF, DOCX, XLSX and CSV are supported")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    originals = args.data_dir / "imports" / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    document_id = uuid.uuid4().hex
    job_id = uuid.uuid4().hex
    stored = originals / f"{document_id}_{source.name}"
    shutil.copy2(source, stored)
    now = now_iso()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    migrate_database(conn)
    subject = conn.execute("SELECT 1 FROM subjects WHERE id=? AND project_id=?", (args.subject_id, args.project_id)).fetchone()
    if not subject:
        raise SystemExit("The subject does not belong to the selected project")
    conn.execute("""INSERT INTO source_documents(id,project_id,subject_id,original_name,stored_path,sha256,
      file_type,size_bytes,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
      (document_id, args.project_id, args.subject_id, source.name, str(stored.relative_to(args.data_dir)),
       hashlib.sha256(stored.read_bytes()).hexdigest(), file_type, stored.stat().st_size, now))
    conn.execute("""INSERT INTO import_jobs(id,source_document_id,project_id,subject_id,status,stage,progress,
      message,parser_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
      (job_id, document_id, args.project_id, args.subject_id, "queued", "parsing", 25,
       "命令行导入等待解析", PARSER_VERSION, now, now))
    conn.commit(); conn.close()

    parse_job(args.db, args.data_dir, job_id)
    conn = sqlite3.connect(args.db); conn.row_factory = sqlite3.Row
    job = conn.execute("SELECT * FROM import_jobs WHERE id=?", (job_id,)).fetchone()
    output = {key: job[key] for key in ("id", "status", "candidate_count", "valid_count", "duplicate_count", "message")}
    if args.commit and job["status"] == "ready":
        output["commit"] = commit_job(conn, job_id, duplicate_action=args.duplicate_action)
        conn.commit()
    conn.close()
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if job["status"] in {"ready", "committed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
