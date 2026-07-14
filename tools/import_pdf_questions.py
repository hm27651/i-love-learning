"""Import H3CSE PDF question banks into the local SQLite database.

Run this with the bundled Codex Python runtime because it includes pdfplumber
and Pillow. Imports are idempotent: PDF filename + question number + occurrence
is the key. The occurrence suffix preserves PDFs that restart their numbering.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pdfplumber
from PIL import Image


QUESTION_RE = re.compile(r"(?m)^(?:QUESTION|问题)\s+(\d+)[ \t]*$")
ANSWER_RE = re.compile(r"(?m)^(?:Correct Answer|正确答案)[:：][ \t]*(.*)$")
OPTION_RE = re.compile(r"(?ms)^([A-Z])\.\s+(.*?)(?=^[A-Z]\.\s+|\Z)")
FIGURE_HINT_RE = re.compile(r"如图|图示|图中|拓扑", re.I)
WATERMARK_SIZE = (221, 60)
SUBJECT_NAMES = {
    "GB0-372": "高级路由交换技术1",
    "GB0-382": "高级路由交换技术2",
    "GB0-392": "网络安全与优化",
}


def clean_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.replace("\x00", "").splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def extract_explanation(block: str, answer_match: re.Match[str]) -> str:
    value = block[answer_match.end() :]
    value = re.sub(r"(?m)^(?:Section|章节)[:：].*$", "", value)
    value = re.sub(r"(?m)^(?:Explanation(?:/Reference:)?|说明/参考[:：]?)[ \t]*$", "", value)
    value = clean_text(value)
    return value if value and value != "--" else "来源 PDF 未提供解析，请人工补充后再核验。"


def parse_pdf(pdf_path: Path) -> tuple[list[dict], list[dict], dict[str, tuple[int, tuple[float, float, float, float], float]]]:
    anomalies: list[dict] = []
    best_images: dict[str, tuple[int, tuple[float, float, float, float], float]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        page_texts = [(page.extract_text() or "") for page in pdf.pages]
        page_starts: list[int] = []
        combined = ""
        for text in page_texts:
            page_starts.append(len(combined))
            combined += text + "\n\n"

        headers = list(QUESTION_RE.finditer(combined))
        records: list[dict] = []
        occurrences: defaultdict[int, int] = defaultdict(int)
        for index, header in enumerate(headers):
            number = int(header.group(1))
            occurrences[number] += 1
            occurrence = occurrences[number]
            record_key = f"{number}:{occurrence}"
            end = headers[index + 1].start() if index + 1 < len(headers) else len(combined)
            block = combined[header.end() : end]
            answer_match = ANSWER_RE.search(block)
            page_no = 1
            for page_index, start in enumerate(page_starts):
                if start <= header.start():
                    page_no = page_index + 1
                else:
                    break
            if not answer_match or not answer_match.group(1).strip():
                anomalies.append({"question": number, "page": page_no, "reason": "blank_answer"})
                continue

            raw_answer = clean_text(answer_match.group(1))
            before_answer = block[: answer_match.start()]
            option_matches = list(OPTION_RE.finditer(before_answer))
            if option_matches:
                options = [clean_text(match.group(2)) for match in option_matches]
                labels = [match.group(1) for match in option_matches]
                answer = "".join(re.findall(r"[A-Z]", raw_answer.upper()))
                if not answer.isalpha() or not set(answer).issubset(set(labels)):
                    anomalies.append({"question": number, "page": page_no, "reason": "invalid_answer", "answer": raw_answer})
                    continue
                stem = clean_text(before_answer[: option_matches[0].start()])
                stem = re.sub(r"^(?:★+[ \t]*\n?)+", "", stem).strip()
                qtype = "multiple" if len(answer) > 1 else "single"
                answers = list(answer)
            else:
                options = []
                stem = clean_text(before_answer)
                stem = re.sub(r"^(?:★+[ \t]*\n?)+", "", stem).strip()
                qtype = "fill"
                answers = [raw_answer]
            records.append(
                {
                    "number": number,
                    "occurrence": occurrence,
                    "key": record_key,
                    "page": page_no,
                    "type": qtype,
                    "stem": stem,
                    "options": options,
                    "answer": answers,
                    "explanation": extract_explanation(block, answer_match),
                }
            )

        active_question: str | None = None
        image_occurrences: defaultdict[int, int] = defaultdict(int)
        for page_index, page in enumerate(pdf.pages):
            events: list[tuple[float, int, object]] = []
            for found in page.search(r"(?:QUESTION|问题)\s+\d+", regex=True) or []:
                match = re.search(r"\d+", found["text"])
                if match:
                    events.append((float(found["top"]), 0, int(match.group())))
            for image in page.images:
                srcsize = tuple(image.get("srcsize") or ())
                if image.get("top", -1) < 0 or srcsize == WATERMARK_SIZE:
                    continue
                x0, top = max(0.0, float(image["x0"])), max(0.0, float(image["top"]))
                x1, bottom = min(float(page.width), float(image["x1"])), min(float(page.height), float(image["bottom"]))
                if x1 <= x0 or bottom <= top:
                    continue
                area = (x1 - x0) * (bottom - top)
                events.append((top, 1, (page_index, (x0, top, x1, bottom), area)))
            for _, kind, payload in sorted(events, key=lambda event: (event[0], event[1])):
                if kind == 0:
                    question_number = int(payload)
                    image_occurrences[question_number] += 1
                    active_question = f"{question_number}:{image_occurrences[question_number]}"
                elif active_question is not None:
                    image_data = payload
                    previous = best_images.get(active_question)
                    if previous is None or image_data[2] > previous[2]:
                        best_images[active_question] = image_data

        complete_records: list[dict] = []
        for record in records:
            if record["stem"]:
                complete_records.append(record)
            elif record["key"] in best_images:
                record["stem"] = f"题干见附图（PDF QUESTION {record['number']}）"
                complete_records.append(record)
            else:
                anomalies.append({"question": record["number"], "page": record["page"], "reason": "blank_stem"})

    return complete_records, anomalies, best_images


def render_images(pdf_path: Path, records: list[dict], best_images: dict, uploads_dir: Path, code: str) -> tuple[dict[str, str], list[dict]]:
    uploads_dir.mkdir(parents=True, exist_ok=True)
    allowed = {record["key"] for record in records}
    records_by_key = {record["key"]: record for record in records}
    result: dict[str, str] = {}
    errors: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for record_key, (page_index, bbox, _) in best_images.items():
            if record_key not in allowed:
                continue
            record = records_by_key[record_key]
            filename = f"pdf_{code}_q{record['number']:04d}_o{record['occurrence']:02d}.png"
            try:
                image = pdf.pages[page_index].crop(bbox).to_image(resolution=180, antialias=True).original.convert("RGB")
                image.save(uploads_dir / filename, format="PNG", optimize=True)
                result[record_key] = filename
            except Exception as exc:  # keep the text import even if one image is malformed
                errors.append({"question": record["number"], "occurrence": record["occurrence"], "page": page_index + 1, "reason": "image_error", "detail": str(exc)})
    return result, errors


def ensure_location(conn: sqlite3.Connection, code: str) -> int:
    project = conn.execute("SELECT id FROM learning_projects WHERE name='H3CSE' ORDER BY id LIMIT 1").fetchone()
    if project is None:
        project = conn.execute("SELECT id FROM learning_projects WHERE status='active' ORDER BY id LIMIT 1").fetchone()
    if project is None:
        raise RuntimeError("请先在“I Love Learning”中创建目标学习项目")
    project_id = project[0]
    subject = conn.execute("SELECT id FROM subjects WHERE project_id=? AND code=?", (project_id, code)).fetchone()
    if subject is None:
        subject_id = conn.execute(
            "INSERT INTO subjects(project_id,name,code) VALUES (?,?,?)",
            (project_id, SUBJECT_NAMES.get(code, f"{code} PDF"), code),
        ).lastrowid
    else:
        subject_id = subject[0]
        conn.execute("UPDATE subjects SET name=? WHERE id=?", (SUBJECT_NAMES.get(code, f"{code} PDF"), subject_id))
    chapter = conn.execute(
        "SELECT id FROM chapters WHERE subject_id=? AND name=?", (subject_id, "PDF 导入（待分类）")
    ).fetchone()
    if chapter is None:
        chapter_id = conn.execute(
            "INSERT INTO chapters(subject_id,name,is_core) VALUES (?,?,0)",
            (subject_id, "PDF 导入（待分类）"),
        ).lastrowid
    else:
        chapter_id = chapter[0]
    point = conn.execute(
        "SELECT id FROM knowledge_points WHERE chapter_id=? AND name=?", (chapter_id, "待分类知识点")
    ).fetchone()
    if point is None:
        return conn.execute(
            "INSERT INTO knowledge_points(chapter_id,name) VALUES (?,?)", (chapter_id, "待分类知识点")
        ).lastrowid
    return point[0]


def backup_database(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pre_pdf_import_{datetime.now():%Y%m%d_%H%M%S}.db"
    source = sqlite3.connect(db_path, timeout=30)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup_path


def import_records(
    conn: sqlite3.Connection,
    pdf_path: Path,
    code: str,
    records: list[dict],
    images: dict[str, str],
) -> dict:
    point_id = ensure_location(conn, code)
    imported = updated = suspected_missing_images = 0
    now = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    for record in records:
        source_key = f"PDF:{pdf_path.name};QUESTION:{record['number']};OCCURRENCE:{record['occurrence']};"
        source = f"{source_key}PAGE:{record['page']}"
        image_path = images.get(record["key"])
        missing_figure = bool(FIGURE_HINT_RE.search(record["stem"])) and not image_path
        suspected_missing_images += int(missing_figure)
        note = (
            "自动从 PDF 文本层导入；答案取自 Correct Answer；题干、选项和命令可能存在原文件排版或 OCR 错误，必须人工核验。"
        )
        if image_path:
            note += " 已自动附加 PDF 中与该题关联的最大位图。"
        elif missing_figure:
            note += " 题干疑似依赖图片，但未检测到可提取位图，请对照原 PDF 补图。"
        existing = conn.execute("SELECT id FROM questions WHERE source LIKE ?", (f"%{source_key}%",)).fetchone()
        values = (
            point_id,
            record["type"],
            record["stem"],
            json.dumps(record["options"], ensure_ascii=False),
            json.dumps(record["answer"], ensure_ascii=False),
            record["explanation"],
            2,
            source,
            note,
            image_path,
            now,
        )
        if existing:
            conn.execute(
                """UPDATE questions SET knowledge_point_id=?,type=?,stem=?,options_json=?,answer_json=?,
                   explanation=?,difficulty=?,source=?,version_note=?,image_path=?,updated_at=? WHERE id=?""",
                values + (existing[0],),
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO questions(knowledge_point_id,type,stem,options_json,answer_json,explanation,
                   difficulty,source,version_note,image_path,status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?,?)""",
                values[:-1] + (now, now),
            )
            imported += 1
    return {
        "imported": imported,
        "updated": updated,
        "suspected_missing_images": suspected_missing_images,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-dir", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--uploads", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args()

    pdf_paths = sorted(args.pdf_dir.glob("*.pdf"))
    if len(pdf_paths) != 3:
        raise SystemExit(f"Expected exactly 3 PDFs, found {len(pdf_paths)}")

    report = {"started_at": datetime.now().isoformat(), "dry_run": args.dry_run,
              "replace_existing": args.replace_existing, "files": []}
    backup = None
    conn = None
    if not args.dry_run:
        backup = backup_database(args.db)
        conn = sqlite3.connect(args.db, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        report["backup"] = str(backup)

    success = False
    try:
        if conn is not None and args.replace_existing:
            project = conn.execute("SELECT id FROM learning_projects WHERE name='H3CSE' ORDER BY id LIMIT 1").fetchone()
            if project is None:
                raise RuntimeError("找不到 H3CSE 学习项目，已停止替换")
            project_id = project[0]
            question_sql = """SELECT q.id FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
              JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=?"""
            question_ids = [row[0] for row in conn.execute(question_sql, (project_id,))]
            report["removed"] = {
                "questions": len(question_ids),
                "attempts": conn.execute(f"SELECT COUNT(*) n FROM attempts WHERE question_id IN ({','.join('?' for _ in question_ids)})", question_ids).fetchone()["n"] if question_ids else 0,
                "practice_sessions": conn.execute("SELECT COUNT(*) n FROM practice_sessions WHERE project_id=?", (project_id,)).fetchone()["n"],
                "mock_exams": conn.execute("SELECT COUNT(*) n FROM mock_exams WHERE project_id=?", (project_id,)).fetchone()["n"],
            }
            if question_ids:
                marks = ",".join("?" for _ in question_ids)
                conn.execute(f"DELETE FROM attempts WHERE question_id IN ({marks})", question_ids)
                conn.execute(f"DELETE FROM question_progress WHERE question_id IN ({marks})", question_ids)
                conn.execute(f"DELETE FROM questions WHERE id IN ({marks})", question_ids)
            conn.execute("DELETE FROM practice_sessions WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM mock_exams WHERE project_id=?", (project_id,))
        for pdf_path in pdf_paths:
            code_match = re.search(r"GB0[-_](\d+)", pdf_path.name, re.I)
            if not code_match:
                raise RuntimeError(f"Cannot find exam code in {pdf_path.name}")
            code = f"GB0-{code_match.group(1)}"
            records, anomalies, best_images = parse_pdf(pdf_path)
            images: dict[str, str] = {}
            if not args.dry_run and not args.no_images:
                images, image_errors = render_images(pdf_path, records, best_images, args.uploads, code)
                anomalies.extend(image_errors)
            stats = {
                "file": pdf_path.name,
                "code": code,
                "parsed": len(records),
                "single": sum(record["type"] == "single" for record in records),
                "multiple": sum(record["type"] == "multiple" for record in records),
                "fill": sum(record["type"] == "fill" for record in records),
                "images": len(images),
                "image_candidates": len(best_images),
                "anomalies": anomalies,
            }
            if conn is not None:
                stats.update(import_records(conn, pdf_path, code, records, images))
            report["files"].append(stats)
            print(json.dumps(stats, ensure_ascii=False))
        if conn is not None:
            conn.commit()
        success = True
    finally:
        if conn is not None:
            if not success:
                conn.rollback()
            conn.close()

    report["completed_at"] = datetime.now().isoformat()
    report["total_parsed"] = sum(item["parsed"] for item in report["files"])
    report["total_imported"] = sum(item.get("imported", 0) for item in report["files"])
    report["total_updated"] = sum(item.get("updated", 0) for item in report["files"])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.report), "total_parsed": report["total_parsed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
