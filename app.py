from __future__ import annotations

import json
import os
import random
import re
import shutil
import socket
import sqlite3
import uuid
import hashlib
from io import BytesIO
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, send_from_directory, session, url_for
from werkzeug.utils import secure_filename

from migrations import LATEST_SCHEMA, PROJECT_MODULE_DEFAULTS, PROJECT_SETTING_DEFAULTS, create_project, migrate_database
from import_service import ImportQueue, PARSER_VERSION, SUPPORTED_TYPES, commit_job
from transfer_service import ExportQueue, cleanup_expired_exports, count_export_questions
from knowledge_service import (
    KnowledgeDeleteError,
    analyze_delete,
    normalize_name,
    perform_delete,
    sibling_name_exists,
)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("STUDY_DATA_DIR") or os.environ.get("H3CSE_DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / os.environ.get("STUDY_DB_NAME", "h3cse.db")
BACKUP_DIR = BASE_DIR / "backups"
APP_PORT = int(os.environ.get("PORT", "23456"))
ALLOWED_IMAGES = {"png", "jpg", "jpeg", "gif", "webp"}
OBJECTIVE_TYPES = {"single", "multiple", "true_false"}

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("STUDY_SECRET") or os.environ.get("H3CSE_SECRET", "study-local-only"),
    MAX_CONTENT_LENGTH=int(os.environ.get("STUDY_MAX_UPLOAD_MB", "50")) * 1024 * 1024,
)


@app.errorhandler(413)
def upload_too_large(_error):
    flash("上传文件超过当前导入上限，请在设置中调整或拆分文件", "error")
    return redirect(url_for("imports_center"))


SCHEMA = LATEST_SCHEMA

DEFAULT_SETTINGS = {
    "intervals": "3,7,14,30",
    "max_import_mb": "50",
}

DEFAULT_WEEKS = [
    (1, "确认范围与工具", "确认学习目标、资料版本并建立知识树", 20, 0, "先验证学习闭环"),
    (2, "第一轮学习块 1", "完成第 1 个学习块", 40, 1, "理解、练习与复盘同步推进"),
    (3, "第一轮学习块 2", "完成第 2 个学习块", 50, 1, "审核新增题目"),
    (4, "第一轮学习块 3", "完成第 3 个学习块", 60, 1, "记录关键验证过程"),
    (5, "第一轮学习块 4", "完成第 4 个学习块", 60, 1, "复盘本阶段错题"),
    (6, "第一轮学习块 5", "完成第 5 个学习块", 70, 1, "补齐薄弱知识点"),
    (7, "首轮覆盖收尾", "完成首轮范围覆盖", 70, 1, "检查题库覆盖率"),
    (8, "第二轮弱项复习", "集中处理掌握度低于目标的章节", 100, 1, "完成跨章节综合实践"),
    (9, "第二轮综合训练", "继续补弱并完成综合练习", 100, 1, "核心章节达到目标"),
    (10, "模拟与复盘 1", "用阶段测试定位弱项并回看知识点", 120, 1, "形成完整复盘记录"),
    (11, "模拟与复盘 2", "清理错题和模糊题", 120, 1, "争取连续达标"),
    (12, "学习目标验收", "检查覆盖、掌握和实践目标", 80, 1, "根据结果决定下一阶段"),
]


@contextmanager
def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
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
        migrate_database(conn)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (key, value))


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def settings(conn):
    return {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM settings")}


def project_settings(conn, project_id):
    values = dict(PROJECT_SETTING_DEFAULTS)
    values.update({row["key"]: row["value"] for row in conn.execute(
        "SELECT key,value FROM project_settings WHERE project_id=?", (project_id,)
    )})
    values["intervals"] = settings(conn).get("intervals", "3,7,14,30")
    return values


def current_project(conn):
    project_id = session.get("current_project_id")
    row = None
    if project_id:
        row = conn.execute(
            "SELECT * FROM learning_projects WHERE id=? AND status='active'", (project_id,)
        ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM learning_projects WHERE status='active' ORDER BY id LIMIT 1"
        ).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM learning_projects ORDER BY id LIMIT 1").fetchone()
    if row is None:
        project_id = create_project(conn, "我的学习项目", "practice")
        row = conn.execute("SELECT * FROM learning_projects WHERE id=?", (project_id,)).fetchone()
    session["current_project_id"] = row["id"]
    return row


def project_modules(conn, project_id):
    return {row["module_key"]: bool(row["enabled"]) for row in conn.execute(
        "SELECT module_key,enabled FROM project_modules WHERE project_id=?", (project_id,)
    )}


def module_enabled(conn, project_id, module_key):
    row = conn.execute(
        "SELECT enabled FROM project_modules WHERE project_id=? AND module_key=?",
        (project_id, module_key),
    ).fetchone()
    return bool(row and row["enabled"])


def module_disabled(project, label):
    return render_template("module_disabled.html", project=project, module_label=label)


def seed_project_weeks(conn, project_id, duration_weeks):
    for week in range(1, duration_weeks + 1):
        source = DEFAULT_WEEKS[week - 1] if week <= len(DEFAULT_WEEKS) else (
            week, f"第 {week} 周学习目标", "按当前项目安排章节学习", 0, 0, ""
        )
        conn.execute(
            """INSERT OR IGNORE INTO weekly_plans(project_id,week_no,title,chapter_goal,question_goal,lab_goal,notes)
               VALUES (?,?,?,?,?,?,?)""",
            (project_id, *source),
        )


def backup_data_snapshot(conn, label):
    target = BACKUP_DIR / f"{label}_{datetime.now():%Y%m%d_%H%M%S}" / "data"
    target.mkdir(parents=True, exist_ok=True)
    backup_db = sqlite3.connect(target / DB_PATH.name)
    try:
        conn.backup(backup_db)
    finally:
        backup_db.close()
    for child in DATA_DIR.iterdir():
        if child.resolve() == DB_PATH.resolve() or child.name in {"h3cse.db-wal", "h3cse.db-shm"}:
            continue
        backup_root = BACKUP_DIR.resolve()
        if backup_root == child.resolve() or backup_root.is_relative_to(child.resolve()):
            continue
        destination = target / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(child, destination)
    return target


def id_belongs_to_project(conn, kind, object_id, project_id):
    queries = {
        "subject": "SELECT 1 FROM subjects WHERE id=? AND project_id=?",
        "chapter": "SELECT 1 FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE c.id=? AND s.project_id=?",
        "point": """SELECT 1 FROM knowledge_points kp JOIN chapters c ON c.id=kp.chapter_id
                     JOIN subjects s ON s.id=c.subject_id WHERE kp.id=? AND s.project_id=?""",
        "question": """SELECT 1 FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
                        JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
                        WHERE q.id=? AND s.project_id=?""",
    }
    return kind in queries and conn.execute(queries[kind], (object_id, project_id)).fetchone() is not None


def knowledge_duplicate_groups(conn, project_id):
    groups = []
    chapter_rows = conn.execute("""SELECT c.id,c.name,c.subject_id,s.name parent_name FROM chapters c
      JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY s.id,c.id""", (project_id,)).fetchall()
    point_rows = conn.execute("""SELECT kp.id,kp.name,kp.chapter_id,c.name parent_name FROM knowledge_points kp
      JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
      WHERE s.project_id=? ORDER BY c.id,kp.id""", (project_id,)).fetchall()
    for kind, rows, parent_key in (("chapter", chapter_rows, "subject_id"), ("point", point_rows, "chapter_id")):
        found = {}
        for row in rows:
            key = (row[parent_key], normalize_name(row["name"]))
            found.setdefault(key, []).append(row)
        for values in found.values():
            if len(values) > 1:
                groups.append({"kind": kind, "name": values[0]["name"], "parent_name": values[0]["parent_name"],
                               "ids": [row["id"] for row in values]})
    return groups


def save_image(file):
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_IMAGES:
        raise ValueError("仅支持 PNG、JPG、GIF、WEBP 图片")
    name = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
    file.save(UPLOAD_DIR / name)
    return name


def parse_options(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_answer(qtype, text):
    value = text.strip()
    if qtype == "multiple":
        return sorted({part.strip().upper() for part in value.replace("，", ",").split(",") if part.strip()})
    if qtype == "true_false":
        return ["true" if value.lower() in {"true", "1", "对", "正确"} else "false"]
    return [value.upper() if qtype == "single" else value]


def question_rows(conn, where="", params=(), limit=None, offset=0):
    sql = """
    SELECT q.*, kp.name knowledge_point_name, c.id chapter_id, c.name chapter_name,
           s.id subject_id, s.project_id, s.name subject_name, s.code exam_code,
           COALESCE(p.mastery_level,0) mastery_level, p.due_date,
           COALESCE(p.error_count,0) error_count, COALESCE(p.attempts,0) attempts
    FROM questions q
    JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
    JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
    LEFT JOIN question_progress p ON p.question_id=q.id
    """ + (" WHERE " + where if where else "") + " ORDER BY q.updated_at DESC"
    query_params = list(params)
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        query_params.extend([limit, offset])
    rows = conn.execute(sql, query_params).fetchall()
    return [dict(r) | {"options": json.loads(r["options_json"]), "answer": json.loads(r["answer_json"])} for r in rows]


def question_count(conn, where="", params=()):
    sql = """SELECT COUNT(*) n FROM questions q
      JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
      JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id"""
    if where:
        sql += " WHERE " + where
    return conn.execute(sql, params).fetchone()["n"]


def get_question(conn, question_id, project_id=None):
    where, params = ["q.id=?"], [question_id]
    if project_id is not None:
        where.append("s.project_id=?")
        params.append(project_id)
    rows = question_rows(conn, " AND ".join(where), params)
    if not rows:
        abort(404)
    return rows[0]


def interval_days(conn, new_level):
    values = [int(x) for x in settings(conn)["intervals"].split(",") if x.strip().isdigit()]
    values = (values + [30, 30, 30, 30])[:4]
    return values[max(0, min(new_level, 4)) - 1] if new_level > 0 else 1


def record_attempt(conn, question, mode, is_correct=None, rating=None, session_id=None):
    existing = conn.execute("SELECT * FROM question_progress WHERE question_id=?", (question["id"],)).fetchone()
    level = existing["mastery_level"] if existing else 0
    attempts = (existing["attempts"] if existing else 0) + 1
    correct_attempts = existing["correct_attempts"] if existing else 0
    errors = existing["error_count"] if existing else 0
    if rating == "fuzzy":
        level = max(0, level - 1)
        due = date.today() + timedelta(days=3)
    elif rating == "unknown" or is_correct is False:
        level = 0
        errors += 1
        due = date.today() + timedelta(days=1)
    else:
        level = min(4, level + 1)
        correct_attempts += 1
        due = date.today() + timedelta(days=interval_days(conn, level))
    conn.execute("""
      INSERT INTO question_progress(question_id,mastery_level,due_date,attempts,correct_attempts,error_count,last_attempt_at)
      VALUES (?,?,?,?,?,?,?)
      ON CONFLICT(question_id) DO UPDATE SET mastery_level=excluded.mastery_level,due_date=excluded.due_date,
      attempts=excluded.attempts,correct_attempts=excluded.correct_attempts,error_count=excluded.error_count,last_attempt_at=excluded.last_attempt_at
    """, (question["id"], level, due.isoformat(), attempts, correct_attempts, errors, now_iso()))
    conn.execute("INSERT INTO attempts(question_id,mode,is_correct,self_rating,answered_at,session_id) VALUES (?,?,?,?,?,?)",
                 (question["id"], mode, None if is_correct is None else int(is_correct), rating, now_iso(), session_id))
    return {"before": existing["mastery_level"] if existing else 0, "after": level, "due_date": due.isoformat()}


def grade_objective(question, form):
    if question["type"] == "multiple":
        submitted = sorted(form.getlist("answer"))
    else:
        submitted = [form.get("answer", "")]
    return submitted == question["answer"], submitted


def chapter_stats(conn, project_id):
    rows = conn.execute("""
      SELECT c.id,c.name,c.is_core,s.name subject_name,s.code exam_code,
        COUNT(q.id) verified_count,
        COUNT(CASE WHEN p.attempts>0 THEN 1 END) attempted_count,
        COALESCE(AVG(CASE WHEN q.id IS NOT NULL THEN COALESCE(p.mastery_level,0) END),0) avg_level,
        COALESCE(SUM(CASE WHEN a.answered_at>=datetime('now','-30 days') AND a.is_correct=1 THEN 1 ELSE 0 END),0) recent_correct,
        COALESCE(SUM(CASE WHEN a.answered_at>=datetime('now','-30 days') AND a.is_correct IS NOT NULL THEN 1 ELSE 0 END),0) recent_total
      FROM chapters c JOIN subjects s ON s.id=c.subject_id
      LEFT JOIN knowledge_points kp ON kp.chapter_id=c.id
      LEFT JOIN questions q ON q.knowledge_point_id=kp.id AND q.status='verified'
      LEFT JOIN question_progress p ON p.question_id=q.id
      LEFT JOIN attempts a ON a.id=(SELECT MAX(a2.id) FROM attempts a2 WHERE a2.question_id=q.id)
      WHERE s.project_id=?
      GROUP BY c.id ORDER BY s.id,c.id
    """, (project_id,)).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        item["coverage"] = round(100 * r["attempted_count"] / r["verified_count"]) if r["verified_count"] else 0
        item["mastery"] = round(25 * r["avg_level"]) if r["verified_count"] else 0
        item["accuracy"] = round(100 * r["recent_correct"] / r["recent_total"]) if r["recent_total"] else 0
        result.append(item)
    return result


def readiness(conn, project_id):
    cfg = project_settings(conn, project_id)
    modules = project_modules(conn, project_id)
    scores = [r["score"] for r in conn.execute("SELECT score FROM mock_exams WHERE project_id=? AND qualifying=1 AND submitted_at IS NOT NULL ORDER BY submitted_at DESC LIMIT 3", (project_id,))]
    mock_ok = len(scores) == 3 and all(x >= float(cfg["mock_threshold"]) for x in scores)
    core = [c for c in chapter_stats(conn, project_id) if c["is_core"]]
    chapter_ok = bool(core) and all(c["mastery"] >= float(cfg["chapter_threshold"]) for c in core)
    lab = conn.execute("SELECT COUNT(*) total,SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) done FROM labs WHERE project_id=?", (project_id,)).fetchone()
    lab_rate = round(100 * (lab["done"] or 0) / lab["total"]) if lab["total"] else 0
    lab_ok = bool(lab["total"]) and lab_rate >= float(cfg["task_threshold"])
    gates = {
        "mock": not (modules.get("mock") and cfg.get("gate_mock_enabled") == "1") or mock_ok,
        "chapter": cfg.get("gate_chapter_enabled") != "1" or chapter_ok,
        "task": not (modules.get("tasks") and cfg.get("gate_task_enabled") == "1") or lab_ok,
    }
    active = {
        "mock": bool(modules.get("mock") and cfg.get("gate_mock_enabled") == "1"),
        "chapter": cfg.get("gate_chapter_enabled") == "1",
        "task": bool(modules.get("tasks") and cfg.get("gate_task_enabled") == "1"),
    }
    return {"ready": any(active.values()) and all(gates.values()), "mock_ok": mock_ok, "chapter_ok": chapter_ok,
            "lab_ok": lab_ok, "scores": scores, "lab_rate": lab_rate, "settings": cfg,
            "gates": gates, "active_gates": active, "gate_count": sum(active[k] and gates[k] for k in active),
            "active_gate_count": sum(active.values())}


def learning_streak(conn, project_id):
    days = {date.fromisoformat(row["day"]) for row in conn.execute(
        """SELECT DISTINCT substr(a.answered_at,1,10) day FROM attempts a JOIN questions q ON q.id=a.question_id
           JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
           JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY day DESC""", (project_id,)
    ) if row["day"]}
    cursor = date.today()
    if cursor not in days:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def attempt_trend(conn, project_id, days=30):
    start = date.today() - timedelta(days=days - 1)
    raw = {row["day"]: dict(row) for row in conn.execute("""
      SELECT substr(answered_at,1,10) day,
        SUM(CASE WHEN is_correct=1 THEN 1 ELSE 0 END) correct,
        SUM(CASE WHEN is_correct=0 THEN 1 ELSE 0 END) wrong,
        COUNT(*) total
      FROM attempts a JOIN questions q ON q.id=a.question_id
      JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
      JOIN subjects s ON s.id=c.subject_id
      WHERE a.answered_at>=? AND s.project_id=? GROUP BY substr(a.answered_at,1,10)
    """, (start.isoformat(), project_id))}
    result = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        item = raw.get(day.isoformat(), {})
        result.append({"day": day.isoformat(), "label": day.strftime("%m/%d"),
                       "correct": item.get("correct", 0) or 0, "wrong": item.get("wrong", 0) or 0,
                       "total": item.get("total", 0) or 0})
    maximum = max((item["total"] for item in result), default=0) or 1
    for item in result:
        item["correct_height"] = round(100 * item["correct"] / maximum)
        item["wrong_height"] = round(100 * item["wrong"] / maximum)
    return result


def subject_stats(conn, project_id):
    rows = conn.execute("""
      SELECT s.id,s.name,s.code exam_code,COUNT(q.id) verified_count,
        COUNT(CASE WHEN p.attempts>0 THEN 1 END) attempted_count,
        COALESCE(AVG(CASE WHEN q.id IS NOT NULL THEN COALESCE(p.mastery_level,0) END),0) avg_level
      FROM subjects s LEFT JOIN chapters c ON c.subject_id=s.id
      LEFT JOIN knowledge_points kp ON kp.chapter_id=c.id
      LEFT JOIN questions q ON q.knowledge_point_id=kp.id AND q.status='verified'
      LEFT JOIN question_progress p ON p.question_id=q.id WHERE s.project_id=? GROUP BY s.id ORDER BY s.id
    """, (project_id,)).fetchall()
    return [dict(row) | {
        "coverage": round(100 * row["attempted_count"] / row["verified_count"]) if row["verified_count"] else 0,
        "mastery": round(25 * row["avg_level"]) if row["verified_count"] else 0,
    } for row in rows]


def active_week(conn, project):
    try:
        start = date.fromisoformat(project["start_date"])
    except ValueError:
        start = date.today()
    week_no = max(1, min(project["duration_weeks"], (date.today() - start).days // 7 + 1))
    plan = conn.execute("SELECT * FROM weekly_plans WHERE project_id=? AND week_no=?", (project["id"], week_no)).fetchone()
    week_start = start + timedelta(days=(week_no - 1) * 7)
    attempts = conn.execute("""SELECT COUNT(*) n FROM attempts a JOIN questions q ON q.id=a.question_id
      JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
      JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND a.answered_at>=?""",
      (project["id"], week_start.isoformat())).fetchone()["n"]
    labs = conn.execute("SELECT COUNT(*) n FROM labs WHERE project_id=? AND status='completed' AND updated_at>=?", (project["id"], week_start.isoformat())).fetchone()["n"]
    return {"week_no": week_no, "plan": plan, "attempts": attempts, "labs": labs,
            "question_rate": min(100, round(100 * attempts / plan["question_goal"])) if plan and plan["question_goal"] else 0,
            "lab_rate": min(100, round(100 * labs / plan["lab_goal"])) if plan and plan["lab_goal"] else 0}


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
            "current_project": project, "available_projects": projects, "current_modules": modules,
            "global_due_count": global_due}


@app.post("/projects/switch")
def switch_project():
    destination = request.form.get("return_to", "/")
    if not destination.startswith("/") or destination.startswith("//"):
        destination = url_for("dashboard")
    if re.match(r"^/(?:questions/(?:review/)?\d+|practice/[0-9a-f-]+|mock/[0-9a-f-]+|labs/\d+)", destination):
        destination = url_for("dashboard")
    with db() as conn:
        project = conn.execute(
            "SELECT id FROM learning_projects WHERE id=? AND status='active'",
            (request.form.get("project_id"),),
        ).fetchone()
        if not project:
            flash("该学习项目不可用，已切换到可用项目", "error")
            session.pop("current_project_id", None)
            current_project(conn)
        else:
            session["current_project_id"] = project["id"]
    return redirect(destination)


@app.route("/projects")
def projects():
    with db() as conn:
        rows = conn.execute("""SELECT p.*,
          (SELECT COUNT(*) FROM subjects s WHERE s.project_id=p.id) subject_count,
          (SELECT COUNT(*) FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
           JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=p.id) question_count
          FROM learning_projects p ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,p.id""").fetchall()
        return render_template("projects.html", projects=rows)


@app.route("/projects/new", methods=["GET", "POST"])
@app.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
def project_form(project_id=None):
    with db() as conn:
        item = conn.execute("SELECT * FROM learning_projects WHERE id=?", (project_id,)).fetchone() if project_id else None
        if project_id and not item:
            abort(404)
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            project_type = request.form.get("project_type", "practice")
            if not name or project_type not in PROJECT_MODULE_DEFAULTS:
                flash("请填写项目名称并选择有效模板", "error")
            else:
                duration = max(1, min(104, request.form.get("duration_weeks", 12, type=int) or 12))
                start_date = request.form.get("start_date") or date.today().isoformat()
                alias = request.form.get("practice_alias", "实践任务").strip() or "实践任务"
                if item:
                    conn.execute("""UPDATE learning_projects SET name=?,project_type=?,description=?,start_date=?,
                      duration_weeks=?,practice_alias=?,updated_at=? WHERE id=?""",
                      (name, project_type, request.form.get("description", "").strip(), start_date,
                       duration, alias, now_iso(), project_id))
                    target_id = project_id
                else:
                    target_id = create_project(conn, name, project_type, start_date, duration, alias)
                    conn.execute("UPDATE learning_projects SET description=? WHERE id=?", (request.form.get("description", "").strip(), target_id))
                defaults = PROJECT_MODULE_DEFAULTS[project_type]
                for key in defaults:
                    enabled = int(request.form.get(f"module_{key}") == "1") if "custom_modules" in request.form else defaults[key]
                    conn.execute("""INSERT INTO project_modules(project_id,module_key,enabled) VALUES (?,?,?)
                      ON CONFLICT(project_id,module_key) DO UPDATE SET enabled=excluded.enabled""", (target_id, key, enabled))
                if module_enabled(conn, target_id, "plan"):
                    seed_project_weeks(conn, target_id, duration)
                session["current_project_id"] = target_id
                flash("学习项目已保存", "success")
                return redirect(url_for("projects"))
        modules = project_modules(conn, project_id) if item else PROJECT_MODULE_DEFAULTS["practice"]
        return render_template("project_form.html", item=item, modules=modules)


@app.post("/projects/<int:project_id>/archive")
def project_archive(project_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM learning_projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            abort(404)
        active_count = conn.execute("SELECT COUNT(*) n FROM learning_projects WHERE status='active'").fetchone()["n"]
        if row["status"] == "active" and active_count <= 1:
            flash("至少保留一个启用中的学习项目", "error")
        else:
            conn.execute("UPDATE learning_projects SET status='archived',updated_at=? WHERE id=?", (now_iso(), project_id))
            if session.get("current_project_id") == project_id:
                session.pop("current_project_id", None)
                current_project(conn)
            flash("项目已归档，题目和进度均已保留", "success")
    return redirect(url_for("projects"))


@app.post("/projects/<int:project_id>/restore")
def project_restore(project_id):
    with db() as conn:
        if not conn.execute("SELECT 1 FROM learning_projects WHERE id=?", (project_id,)).fetchone():
            abort(404)
        conn.execute("UPDATE learning_projects SET status='active',updated_at=? WHERE id=?", (now_iso(), project_id))
    flash("项目已恢复", "success")
    return redirect(url_for("projects"))


@app.post("/projects/<int:project_id>/delete")
def project_delete(project_id):
    if request.form.get("confirmation", "").strip() != "永久删除":
        flash("请输入“永久删除”进行二次确认", "error")
        return redirect(url_for("projects"))
    with db() as conn:
        project = conn.execute("SELECT * FROM learning_projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            abort(404)
        if conn.execute("SELECT COUNT(*) n FROM learning_projects").fetchone()["n"] <= 1:
            flash("不能永久删除最后一个学习项目", "error")
            return redirect(url_for("projects"))
        backup = backup_data_snapshot(conn, f"pre_delete_project_{project_id}")
        qids = [row[0] for row in conn.execute("""SELECT q.id FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
          JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=?""", (project_id,))]
        if qids:
            marks = ",".join("?" for _ in qids)
            conn.execute(f"DELETE FROM attempts WHERE question_id IN ({marks})", qids)
            conn.execute(f"DELETE FROM question_progress WHERE question_id IN ({marks})", qids)
            conn.execute(f"UPDATE import_candidates SET duplicate_question_id=NULL WHERE duplicate_question_id IN ({marks})", qids)
            conn.execute(f"DELETE FROM questions WHERE id IN ({marks})", qids)
        document_paths = [row[0] for row in conn.execute("SELECT stored_path FROM source_documents WHERE project_id=?", (project_id,))]
        conn.execute("DELETE FROM import_candidates WHERE job_id IN (SELECT id FROM import_jobs WHERE project_id=?)", (project_id,))
        conn.execute("DELETE FROM import_jobs WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM source_documents WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM mock_exams WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM practice_sessions WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM labs WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM weekly_plans WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM knowledge_points WHERE chapter_id IN (SELECT c.id FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=?)", (project_id,))
        conn.execute("DELETE FROM chapters WHERE subject_id IN (SELECT id FROM subjects WHERE project_id=?)", (project_id,))
        conn.execute("DELETE FROM subjects WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM project_modules WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM project_settings WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM learning_projects WHERE id=?", (project_id,))
        if session.get("current_project_id") == project_id:
            session.pop("current_project_id", None)
            current_project(conn)
        for stored in document_paths:
            path = DATA_DIR / stored
            if path.is_file():
                path.unlink()
    flash(f"项目已永久删除；删除前备份保存在 {backup}", "success")
    return redirect(url_for("projects"))


@app.route("/")
def dashboard():
    with db() as conn:
        project = current_project(conn); project_id = project["id"]
        due = conn.execute("""SELECT COUNT(*) n FROM question_progress p JOIN questions q ON q.id=p.question_id
          JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
          JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND q.status='verified' AND p.due_date<=?""",
          (project_id, date.today().isoformat())).fetchone()["n"]
        errors = conn.execute("""SELECT COUNT(*) n FROM question_progress p JOIN questions q ON q.id=p.question_id
          JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
          JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND p.error_count>0""", (project_id,)).fetchone()["n"]
        mocks = conn.execute("SELECT * FROM mock_exams WHERE project_id=? AND submitted_at IS NOT NULL ORDER BY submitted_at DESC LIMIT 5", (project_id,)).fetchall()
        sessions = conn.execute("SELECT * FROM practice_sessions WHERE project_id=? AND completed_at IS NULL ORDER BY started_at DESC LIMIT 10", (project_id,)).fetchall()
        continue_session = next((s for s in sessions if s["current_index"] < len(json.loads(s["question_ids_json"]))), None)
        last_seven = conn.execute("""SELECT COUNT(*) n FROM attempts a JOIN questions q ON q.id=a.question_id
          JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
          JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND a.answered_at>=?""",
          (project_id, (date.today()-timedelta(days=6)).isoformat())).fetchone()["n"]
        return render_template("dashboard.html", chapters=chapter_stats(conn, project_id), due=due, errors=errors,
                               mocks=mocks, readiness=readiness(conn, project_id), week=active_week(conn, project),
                               streak=learning_streak(conn, project_id), last_seven=last_seven,
                               continue_session=continue_session, trend=attempt_trend(conn, project_id, 14), project=project)


@app.route("/progress")
def progress():
    with db() as conn:
        project = current_project(conn); project_id = project["id"]
        return render_template("progress.html", chapters=chapter_stats(conn, project_id), subjects=subject_stats(conn, project_id),
                               mocks=conn.execute("SELECT * FROM mock_exams WHERE project_id=? AND submitted_at IS NOT NULL ORDER BY submitted_at DESC LIMIT 12", (project_id,)).fetchall(),
                               readiness=readiness(conn, project_id), trend=attempt_trend(conn, project_id, 30),
                               streak=learning_streak(conn, project_id), week=active_week(conn, project), project=project)


def lan_url():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
    except OSError:
        ip = "本机局域网IP"
    return f"http://{ip}:{APP_PORT}"


@app.route("/uploads/<path:name>")
def upload(name):
    with db() as conn:
        project_id = current_project(conn)["id"]
        allowed = conn.execute("""SELECT 1 FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
          JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
          WHERE q.image_path=? AND s.project_id=? UNION ALL
          SELECT 1 FROM labs l WHERE l.image_path=? AND l.project_id=? LIMIT 1""",
          (name, project_id, name, project_id)).fetchone()
        if not allowed:
            abort(404)
    return send_from_directory(UPLOAD_DIR, name)


@app.route("/knowledge", methods=["GET", "POST"])
def knowledge():
    with db() as conn:
        project = current_project(conn); project_id = project["id"]
        if request.method == "POST":
            kind = request.form["kind"]
            name = request.form.get("name", "").strip()
            created_id = None
            if not name:
                flash("名称不能为空", "error")
            elif kind == "subject":
                try:
                    created_id = conn.execute(
                        "INSERT INTO subjects(project_id,name,code) VALUES (?,?,?)",
                        (project_id, name, request.form.get("exam_code", "").strip()),
                    ).lastrowid
                except sqlite3.IntegrityError:
                    flash("同一项目内科目代码不能重复", "error")
                    return redirect(url_for("knowledge"))
            elif kind == "chapter":
                if not id_belongs_to_project(conn, "subject", request.form["subject_id"], project_id): abort(404)
                subject_id = int(request.form["subject_id"])
                if sibling_name_exists(conn, "chapter", subject_id, name):
                    flash("同一科目内不能存在同名章节", "error")
                    return redirect(url_for("knowledge"))
                created_id = conn.execute(
                    "INSERT INTO chapters(subject_id,name,is_core) VALUES (?,?,?)",
                    (subject_id, name, int("is_core" in request.form)),
                ).lastrowid
            elif kind == "point":
                if not id_belongs_to_project(conn, "chapter", request.form["chapter_id"], project_id): abort(404)
                chapter_id = int(request.form["chapter_id"])
                if sibling_name_exists(conn, "point", chapter_id, name):
                    flash("同一章节内不能存在同名知识点", "error")
                    return redirect(url_for("knowledge"))
                created_id = conn.execute(
                    "INSERT INTO knowledge_points(chapter_id,name) VALUES (?,?)", (chapter_id, name)
                ).lastrowid
            flash("知识树已更新", "success")
            if created_id:
                return redirect(url_for("knowledge", focus_kind=kind, focus_id=created_id))
            return redirect(url_for("knowledge"))
        subjects = conn.execute("""SELECT s.id,s.project_id,s.name,s.code exam_code,
          (SELECT COUNT(*) FROM chapters c WHERE c.subject_id=s.id) chapter_count,
          (SELECT COUNT(*) FROM knowledge_points kp JOIN chapters c ON c.id=kp.chapter_id
            WHERE c.subject_id=s.id) point_count,
          (SELECT COUNT(*) FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
            JOIN chapters c ON c.id=kp.chapter_id WHERE c.subject_id=s.id) question_count
          FROM subjects s WHERE s.project_id=? ORDER BY s.id""", (project_id,)).fetchall()
        chapters = conn.execute("""SELECT c.*,s.name subject_name,
          (SELECT COUNT(*) FROM knowledge_points kp WHERE kp.chapter_id=c.id) point_count,
          (SELECT COUNT(*) FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
            WHERE kp.chapter_id=c.id) question_count
          FROM chapters c JOIN subjects s ON s.id=c.subject_id
          WHERE s.project_id=? ORDER BY s.id,c.id""", (project_id,)).fetchall()
        points = conn.execute("""SELECT kp.*,c.name chapter_name,
          (SELECT COUNT(*) FROM questions q WHERE q.knowledge_point_id=kp.id) question_count
          FROM knowledge_points kp JOIN chapters c ON c.id=kp.chapter_id
          JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY c.id,kp.id""", (project_id,)).fetchall()
        overview = {
            "subjects": len(subjects),
            "chapters": len(chapters),
            "points": len(points),
            "questions": sum(row["question_count"] for row in points),
        }
        return render_template("knowledge.html", subjects=subjects, chapters=chapters, points=points, project=project,
                               overview=overview, duplicate_groups=knowledge_duplicate_groups(conn, project_id))


@app.post("/knowledge/chapter/<int:chapter_id>/toggle-core")
def toggle_core(chapter_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        if not id_belongs_to_project(conn, "chapter", chapter_id, project_id): abort(404)
        conn.execute("UPDATE chapters SET is_core=1-is_core WHERE id=?", (chapter_id,))
    return redirect(url_for("knowledge", focus_kind="chapter", focus_id=chapter_id))


@app.post("/knowledge/rename")
def knowledge_rename():
    allowed = {"subject": "subjects", "chapter": "chapters", "point": "knowledge_points"}
    kind = request.form.get("kind")
    name = request.form.get("name", "").strip()
    if kind not in allowed or not name: abort(400)
    with db() as conn:
        project_id = current_project(conn)["id"]
        if not id_belongs_to_project(conn, kind, request.form["id"], project_id): abort(404)
        if kind == "subject":
            try:
                conn.execute("UPDATE subjects SET name=?,code=? WHERE id=?", (name, request.form.get("exam_code", "").strip(), request.form["id"]))
            except sqlite3.IntegrityError:
                flash("同一项目内科目代码不能重复", "error")
                return redirect(url_for("knowledge"))
        else:
            if kind == "chapter":
                row = conn.execute("SELECT subject_id FROM chapters WHERE id=?", (request.form["id"],)).fetchone()
                if sibling_name_exists(conn, "chapter", row["subject_id"], name, int(request.form["id"])):
                    flash("同一科目内不能存在同名章节", "error")
                    return redirect(url_for("knowledge"))
                conn.execute(
                    "UPDATE chapters SET name=?,is_core=? WHERE id=?",
                    (name, int("is_core" in request.form), request.form["id"]),
                )
            else:
                row = conn.execute("SELECT chapter_id FROM knowledge_points WHERE id=?", (request.form["id"],)).fetchone()
                if sibling_name_exists(conn, "point", row["chapter_id"], name, int(request.form["id"])):
                    flash("同一章节内不能存在同名知识点", "error")
                    return redirect(url_for("knowledge"))
                conn.execute("UPDATE knowledge_points SET name=? WHERE id=?", (name, request.form["id"]))
    flash("名称已更新", "success")
    return redirect(url_for("knowledge", focus_kind=kind, focus_id=request.form["id"]))


@app.get("/api/knowledge/<kind>/<int:node_id>/delete-impact")
def knowledge_delete_impact(kind, node_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        try:
            impact = analyze_delete(conn, project_id, kind, node_id, request.args.get("target_id", type=int))
        except KnowledgeDeleteError as exc:
            return {"error": str(exc)}, 400
        return impact


@app.post("/knowledge/delete")
def knowledge_delete():
    kind = request.form.get("kind", "")
    node_id = request.form.get("node_id", type=int)
    target_id = request.form.get("target_id", type=int)
    if not node_id:
        abort(400)
    with db() as conn:
        project_id = current_project(conn)["id"]
        try:
            result = perform_delete(
                conn,
                project_id,
                kind,
                node_id,
                target_id=target_id,
                merge_conflicts=request.form.get("merge_conflicts") == "1",
                confirmation_name=request.form.get("confirmation_name", ""),
                db_path=DB_PATH,
                backup_dir=BACKUP_DIR,
            )
        except KnowledgeDeleteError as exc:
            flash(str(exc), "error")
            return redirect(url_for("knowledge"))
        except (sqlite3.Error, OSError) as exc:
            flash(f"删除未完成，所有数据库修改均已回滚：{exc}", "error")
            return redirect(url_for("knowledge"))
    backup = result["backup_folder"]
    message = f"已删除“{result['source']['name']}”"
    if result["target"]:
        message += f"，内容已迁移到“{result['target']['name']}”"
    if backup:
        message += f"；备份：{backup}"
    flash(message, "success")
    if result["target"]:
        return redirect(url_for("knowledge", focus_kind=kind, focus_id=result["target"]["id"]))
    return redirect(url_for("knowledge"))


@app.route("/questions")
def questions():
    with db() as conn:
        project_id = current_project(conn)["id"]
        where, params = ["s.project_id=?"], [project_id]
        if request.args.get("q"):
            where.append("(q.stem LIKE ? OR q.explanation LIKE ?)")
            term = f"%{request.args['q']}%"; params += [term, term]
        for key, column in (("status", "q.status"), ("type", "q.type"), ("subject_id", "s.id"),
                            ("chapter_id", "c.id"), ("knowledge_point_id", "kp.id")):
            if request.args.get(key): where.append(f"{column}=?"); params.append(request.args[key])
        if request.args.get("import_batch_id"):
            where.append("q.import_batch_id=?"); params.append(request.args["import_batch_id"])
        if request.args.get("classification") == "uncategorized":
            where.append("(c.name='待分类' OR kp.name='待分类')")
        page = max(1, request.args.get("page", 1, type=int))
        per_page = 50
        total = question_count(conn, " AND ".join(where), params)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        subjects = conn.execute("SELECT id,name,code exam_code FROM subjects WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        chapters = conn.execute("SELECT c.id,c.name FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY c.id", (project_id,)).fetchall()
        points = conn.execute("""SELECT kp.id,kp.name,c.name chapter_name,s.name subject_name FROM knowledge_points kp
          JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
          WHERE s.project_id=? ORDER BY s.id,c.id,kp.id""", (project_id,)).fetchall()
        rows = question_rows(conn, " AND ".join(where), params, limit=per_page, offset=(page - 1) * per_page)
        return render_template("questions.html", questions=rows, subjects=subjects, chapters=chapters, points=points, total=total, page=page, pages=pages)


@app.route("/questions/new", methods=["GET", "POST"])
@app.route("/questions/<int:question_id>/edit", methods=["GET", "POST"])
def question_form(question_id=None):
    with db() as conn:
        project_id = current_project(conn)["id"]
        item = get_question(conn, question_id, project_id) if question_id else None
        if request.method == "POST":
            qtype = request.form["type"]
            options = parse_options(request.form.get("options", "")) if qtype in OBJECTIVE_TYPES else []
            if qtype in {"single", "multiple"} and len(options) < 2:
                flash("单选和多选至少需要两个选项", "error")
            else:
                if not id_belongs_to_project(conn, "point", request.form["knowledge_point_id"], project_id): abort(404)
                try: image = save_image(request.files.get("image"))
                except ValueError as exc:
                    flash(str(exc), "error"); image = None
                values = (request.form["knowledge_point_id"], qtype, request.form["stem"].strip(), json.dumps(options, ensure_ascii=False),
                          json.dumps(parse_answer(qtype, request.form.get("answer", "")), ensure_ascii=False), request.form.get("explanation", "").strip(),
                          int(request.form.get("difficulty", 2)), request.form.get("source", "").strip(), request.form.get("version_note", "").strip(),
                          image or (item["image_path"] if item else None), request.form.get("status", "draft"), now_iso())
                if item:
                    conn.execute("""UPDATE questions SET knowledge_point_id=?,type=?,stem=?,options_json=?,answer_json=?,explanation=?,difficulty=?,source=?,version_note=?,image_path=?,status=?,updated_at=? WHERE id=?""", values + (question_id,))
                else:
                    conn.execute("""INSERT INTO questions(knowledge_point_id,type,stem,options_json,answer_json,explanation,difficulty,source,version_note,image_path,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", values[:-1] + (values[-1], values[-1]))
                flash("题目已保存", "success")
                return redirect(url_for("questions"))
        points = conn.execute("""SELECT kp.id,kp.name,c.name chapter_name,s.name subject_name FROM knowledge_points kp
          JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
          WHERE s.project_id=? ORDER BY s.id,c.id,kp.id""", (project_id,)).fetchall()
        return render_template("question_form.html", item=item, points=points)


@app.post("/questions/<int:question_id>/status/<status>")
def question_status(question_id, status):
    if status not in {"draft", "verified", "archived"}: abort(400)
    with db() as conn:
        project_id = current_project(conn)["id"]
        if not id_belongs_to_project(conn, "question", question_id, project_id): abort(404)
        conn.execute("UPDATE questions SET status=?,updated_at=? WHERE id=?", (status, now_iso(), question_id))
    return redirect(url_for("questions"))


@app.post("/questions/bulk")
def questions_bulk():
    action = request.form.get("action")
    ids = [int(value) for value in request.form.getlist("question_id") if value.isdigit()]
    return_to = request.form.get("return_to", "")
    destination = return_to if return_to.startswith("/") and not return_to.startswith("//") else url_for("questions")
    if action not in {"draft", "verified", "archived", "move"} or not ids:
        flash("请先选择题目和批量操作", "error")
        return redirect(destination)
    with db() as conn:
        project_id = current_project(conn)["id"]
        valid_ids = [value for value in ids if id_belongs_to_project(conn, "question", value, project_id)]
        if valid_ids and action == "move":
            point_id = request.form.get("target_point_id", type=int)
            if not point_id or not id_belongs_to_project(conn, "point", point_id, project_id):
                flash("请选择当前项目中的目标知识点", "error")
                return redirect(destination)
            conn.execute("UPDATE questions SET knowledge_point_id=?,updated_at=? WHERE id IN (%s)" % ",".join("?" for _ in valid_ids),
                         [point_id, now_iso(), *valid_ids])
        elif valid_ids:
            conn.execute("UPDATE questions SET status=?,updated_at=? WHERE id IN (%s)" % ",".join("?" for _ in valid_ids),
                         [action, now_iso(), *valid_ids])
    flash(f"已更新 {len(valid_ids)} 道题目", "success")
    return redirect(destination)


@app.route("/questions/review/<int:question_id>", methods=["GET", "POST"])
def question_review(question_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        current = get_question(conn, question_id, project_id)
        if request.method == "POST":
            action = request.form.get("action")
            if action in {"draft", "verified", "archived"}:
                conn.execute("UPDATE questions SET status=?,updated_at=? WHERE id=?", (action, now_iso(), question_id))
                flash("审核状态已更新", "success")
            next_id = request.form.get("next_id", type=int)
            if next_id:
                return redirect(url_for("question_review", question_id=next_id, **request.args))
            return redirect(url_for("questions", **request.args))

        where, params = ["s.project_id=?"], [project_id]
        if request.args.get("q"):
            where.append("(q.stem LIKE ? OR q.explanation LIKE ?)")
            term = f"%{request.args['q']}%"; params += [term, term]
        for key, column in (("status", "q.status"), ("type", "q.type"), ("subject_id", "s.id"),
                            ("chapter_id", "c.id"), ("knowledge_point_id", "kp.id")):
            if request.args.get(key): where.append(f"{column}=?"); params.append(request.args[key])
        if request.args.get("import_batch_id"):
            where.append("q.import_batch_id=?"); params.append(request.args["import_batch_id"])
        if request.args.get("classification") == "uncategorized":
            where.append("(c.name='待分类' OR kp.name='待分类')")
        page = max(1, request.args.get("page", 1, type=int))
        queue = question_rows(conn, " AND ".join(where), params, limit=50, offset=(page - 1) * 50)
        ids = [item["id"] for item in queue]
        if question_id in ids:
            position = ids.index(question_id)
            previous_id = ids[position - 1] if position else None
            next_id = ids[position + 1] if position + 1 < len(ids) else None
        else:
            previous_id = next_id = None
            queue = [current]
            position = 0
        return render_template("question_review.html", question=current, queue=queue,
                               position=position, previous_id=previous_id, next_id=next_id)


def practice_filter_clause(values, project_id):
    where, params = ["q.status='verified'", "s.project_id=?"], [project_id]
    for key, column in (("subject_id", "s.id"), ("chapter_id", "c.id"),
                        ("knowledge_point_id", "kp.id"), ("type", "q.type")):
        if values.get(key):
            where.append(f"{column}=?")
            params.append(values[key])
    return " AND ".join(where), params


@app.get("/api/practice/count")
def practice_match_count():
    with db() as conn:
        project_id = current_project(conn)["id"]
        where, params = practice_filter_clause(request.args, project_id)
        return {"count": question_count(conn, where, params)}


@app.route("/practice", methods=["GET", "POST"])
def practice():
    with db() as conn:
        project_id = current_project(conn)["id"]
        if request.method == "POST":
            where, params = practice_filter_clause(request.form, project_id)
            rows = question_rows(conn, where, params)
            random.shuffle(rows)
            if request.form.get("selection_mode", "count") != "all":
                requested_count = request.form.get("count", 10, type=int) or 10
                rows = rows[:max(1, min(100, requested_count))]
            if not rows:
                flash("没有符合条件的已核验题目", "error"); return redirect(url_for("practice"))
            sid = uuid.uuid4().hex
            conn.execute("INSERT INTO practice_sessions(id,project_id,mode,question_ids_json,started_at) VALUES (?,?,?,?,?)", (sid, project_id, "practice", json.dumps([r["id"] for r in rows]), now_iso()))
            return redirect(url_for("practice_run", session_id=sid))
        subjects = conn.execute("SELECT id,project_id,name,code exam_code FROM subjects WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
        chapters = conn.execute("SELECT c.* FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY c.id", (project_id,)).fetchall()
        points = conn.execute("""SELECT kp.* FROM knowledge_points kp JOIN chapters c ON c.id=kp.chapter_id
          JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY kp.id""", (project_id,)).fetchall()
        return render_template("practice_setup.html", subjects=subjects, chapters=chapters, points=points)


@app.route("/practice/<session_id>", methods=["GET", "POST"])
def practice_run(session_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        session = conn.execute("SELECT * FROM practice_sessions WHERE id=? AND project_id=?", (session_id, project_id)).fetchone()
        if not session: abort(404)
        ids = json.loads(session["question_ids_json"]); index = session["current_index"]
        if index >= len(ids):
            conn.execute("UPDATE practice_sessions SET completed_at=COALESCE(completed_at,?) WHERE id=?", (now_iso(), session_id))
            return render_template("practice_complete.html", total=len(ids), mode=session["mode"])
        question = get_question(conn, ids[index], project_id)
        feedback = None
        if request.method == "POST":
            if request.form.get("action") == "next":
                conn.execute("UPDATE practice_sessions SET current_index=current_index+1 WHERE id=?", (session_id,))
                return redirect(url_for("practice_run", session_id=session_id))
            if question["type"] in OBJECTIVE_TYPES:
                correct, submitted = grade_objective(question, request.form)
                progress_change = record_attempt(conn, question, session["mode"], is_correct=correct, session_id=session_id)
                feedback = {"correct": correct, "submitted": submitted, "progress": progress_change}
            else:
                rating = request.form.get("rating")
                if rating:
                    progress_change = record_attempt(conn, question, session["mode"], rating=rating, session_id=session_id)
                    feedback = {"rating": rating, "progress": progress_change}
                else:
                    feedback = {"reveal": True}
        return render_template("practice_question.html", question=question, feedback=feedback, index=index,
                               total=len(ids), session_id=session_id, mode=session["mode"])


@app.route("/review", methods=["GET", "POST"])
def review():
    with db() as conn:
        project_id = current_project(conn)["id"]
        where = "q.status='verified' AND p.attempts>0 AND s.project_id=?"
        params = [project_id]
        mode = request.args.get("filter", "due")
        if mode == "due": where += " AND p.due_date<=?"; params.append(date.today().isoformat())
        elif mode == "errors": where += " AND p.error_count>0"
        if request.args.get("chapter_id"): where += " AND c.id=?"; params.append(request.args["chapter_id"])
        rows = question_rows(conn, where, params)
        rows.sort(key=lambda item: (item["due_date"] or "9999-12-31", -item["error_count"], item["mastery_level"]))
        if request.method == "POST":
            ids = [int(x) for x in request.form.getlist("question_id")]
            if not ids: flash("请选择至少一道题", "error"); return redirect(request.url)
            sid = uuid.uuid4().hex
            valid_ids = [value for value in ids if id_belongs_to_project(conn, "question", value, project_id)]
            if not valid_ids: abort(400)
            conn.execute("INSERT INTO practice_sessions(id,project_id,mode,question_ids_json,started_at) VALUES (?,?,?,?,?)", (sid, project_id, "review", json.dumps(valid_ids), now_iso()))
            return redirect(url_for("practice_run", session_id=sid))
        chapters = conn.execute("SELECT c.id,c.name FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY c.id", (project_id,)).fetchall()
        counts = {
            "due": conn.execute("""SELECT COUNT(*) n FROM question_progress p JOIN questions q ON q.id=p.question_id
              JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
              JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND q.status='verified' AND p.due_date<=?""", (project_id, date.today().isoformat())).fetchone()["n"],
            "errors": conn.execute("""SELECT COUNT(*) n FROM question_progress p JOIN questions q ON q.id=p.question_id
              JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
              JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND q.status='verified' AND p.error_count>0""", (project_id,)).fetchone()["n"],
        }
        return render_template("review.html", questions=rows, chapters=chapters, filter=mode, counts=counts)


@app.route("/labs")
def labs():
    with db() as conn:
        project = current_project(conn); project_id = project["id"]
        if not module_enabled(conn, project_id, "tasks"): return module_disabled(project, project["practice_alias"])
        rows = conn.execute("""SELECT l.*,c.name chapter_name,s.name subject_name FROM labs l
          JOIN chapters c ON c.id=l.chapter_id JOIN subjects s ON s.id=c.subject_id
          WHERE l.project_id=? ORDER BY CASE l.status WHEN 'doing' THEN 0 WHEN 'planned' THEN 1 ELSE 2 END,l.due_date,l.id""", (project_id,)).fetchall()
        return render_template("labs.html", labs=rows, task_alias=project["practice_alias"])


@app.route("/labs/new", methods=["GET", "POST"])
@app.route("/labs/<int:lab_id>/edit", methods=["GET", "POST"])
def lab_form(lab_id=None):
    with db() as conn:
        project = current_project(conn); project_id = project["id"]
        if not module_enabled(conn, project_id, "tasks"): return module_disabled(project, project["practice_alias"])
        item = conn.execute("SELECT * FROM labs WHERE id=? AND project_id=?", (lab_id, project_id)).fetchone() if lab_id else None
        if lab_id and not item: abort(404)
        if request.method == "POST":
            if not id_belongs_to_project(conn, "chapter", request.form["chapter_id"], project_id): abort(404)
            try: image = save_image(request.files.get("image"))
            except ValueError as exc:
                flash(str(exc), "error"); image = None
            values = (request.form["chapter_id"], request.form["title"].strip(), request.form.get("objective", "").strip(),
                      request.form.get("topology_file_path", "").strip(), request.form.get("commands", "").strip(),
                      request.form.get("verification", "").strip(), request.form.get("result", "").strip(),
                      image or (item["image_path"] if item else None), request.form.get("status", "planned"),
                      request.form.get("due_date") or None, now_iso())
            if item:
                conn.execute("""UPDATE labs SET chapter_id=?,title=?,objective=?,topology_file_path=?,commands=?,verification=?,result=?,image_path=?,status=?,due_date=?,updated_at=? WHERE id=?""", values + (lab_id,))
            else:
                conn.execute("""INSERT INTO labs(project_id,chapter_id,title,objective,topology_file_path,commands,verification,result,image_path,status,due_date,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (project_id,) + values[:-1] + (values[-1], values[-1]))
            flash(f"{project['practice_alias']}记录已保存", "success")
            return redirect(url_for("labs"))
        chapters = conn.execute("""SELECT c.id,c.name,s.name subject_name FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY s.id,c.id""", (project_id,)).fetchall()
        return render_template("lab_form.html", item=item, chapters=chapters, task_alias=project["practice_alias"])


@app.post("/labs/<int:lab_id>/complete")
def lab_complete(lab_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        row = conn.execute("SELECT 1 FROM labs WHERE id=? AND project_id=?", (lab_id, project_id)).fetchone()
        if not row: abort(404)
        conn.execute("UPDATE labs SET status='completed',updated_at=? WHERE id=?", (now_iso(), lab_id))
    return redirect(url_for("labs"))


@app.route("/plans", methods=["GET", "POST"])
def plans():
    with db() as conn:
        project = current_project(conn); project_id = project["id"]
        if not module_enabled(conn, project_id, "plan"): return module_disabled(project, "学习计划")
        if request.method == "POST":
            week = max(1, min(project["duration_weeks"], int(request.form["week_no"])))
            conn.execute("""INSERT INTO weekly_plans(project_id,week_no,title,chapter_goal,question_goal,lab_goal,notes)
              VALUES (?,?,?,?,?,?,?) ON CONFLICT(project_id,week_no) DO UPDATE SET title=excluded.title,chapter_goal=excluded.chapter_goal,
              question_goal=excluded.question_goal,lab_goal=excluded.lab_goal,notes=excluded.notes""",
              (project_id, week, request.form["title"].strip(), request.form.get("chapter_goal", "").strip(),
               max(0, int(request.form.get("question_goal", 0))), max(0, int(request.form.get("lab_goal", 0))), request.form.get("notes", "").strip()))
            flash(f"第 {week} 周目标已保存", "success")
            return redirect(url_for("plans"))
        seed_project_weeks(conn, project_id, project["duration_weeks"])
        rows = conn.execute("SELECT * FROM weekly_plans WHERE project_id=? ORDER BY week_no", (project_id,)).fetchall()
        completed_questions = conn.execute("""SELECT COUNT(*) n FROM attempts a JOIN questions q ON q.id=a.question_id
          JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
          JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=?""", (project_id,)).fetchone()["n"]
        completed_labs = conn.execute("SELECT COUNT(*) n FROM labs WHERE project_id=? AND status='completed'", (project_id,)).fetchone()["n"]
        return render_template("plans.html", plans=rows, completed_questions=completed_questions, completed_labs=completed_labs, project=project)


@app.route("/mock", methods=["GET", "POST"])
def mock_setup():
    with db() as conn:
        project = current_project(conn); project_id = project["id"]
        if not module_enabled(conn, project_id, "mock"): return module_disabled(project, "模拟考试")
        cfg = project_settings(conn, project_id)
        if request.method == "POST":
            count = max(1, min(200, int(request.form.get("count", cfg["qualifying_count"]))))
            minutes = max(1, min(300, int(request.form.get("minutes", cfg["qualifying_minutes"]))))
            chapter_ids = [int(x) for x in request.form.getlist("chapter_id")]
            where, params = ["q.status='verified'", "q.type IN ('single','multiple','true_false')", "s.project_id=?"], [project_id]
            if chapter_ids:
                where.append("c.id IN (%s)" % ",".join("?" for _ in chapter_ids)); params.extend(chapter_ids)
            rows = question_rows(conn, " AND ".join(where), params)
            random.shuffle(rows); chosen = rows[:count]
            if len(chosen) < count:
                flash(f"符合条件的已核验客观题只有 {len(chosen)} 道，无法生成 {count} 道试卷", "error")
                return redirect(url_for("mock_setup"))
            core_ids = {r["id"] for r in conn.execute("SELECT c.id FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND c.is_core=1", (project_id,))}
            qualifying = count >= int(cfg["qualifying_count"]) and minutes == int(cfg["qualifying_minutes"]) and core_ids.issubset(set(chapter_ids) if chapter_ids else core_ids)
            exam_id = uuid.uuid4().hex
            conn.execute("INSERT INTO mock_exams(id,project_id,started_at,question_ids_json,objective_count,time_limit,qualifying,chapter_ids_json) VALUES (?,?,?,?,?,?,?,?)",
                         (exam_id, project_id, now_iso(), json.dumps([r["id"] for r in chosen]), count, minutes, int(qualifying), json.dumps(chapter_ids)))
            return redirect(url_for("mock_exam", exam_id=exam_id))
        chapters = conn.execute("""SELECT c.*,s.name subject_name,(SELECT COUNT(*) FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id WHERE kp.chapter_id=c.id AND q.status='verified' AND q.type IN ('single','multiple','true_false')) question_count FROM chapters c JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY s.id,c.id""", (project_id,)).fetchall()
        history = conn.execute("SELECT * FROM mock_exams WHERE project_id=? AND submitted_at IS NOT NULL ORDER BY submitted_at DESC LIMIT 10", (project_id,)).fetchall()
        return render_template("mock_setup.html", chapters=chapters, history=history, cfg=cfg)


@app.route("/mock/<exam_id>", methods=["GET", "POST"])
def mock_exam(exam_id):
    with db() as conn:
        project_id = current_project(conn)["id"]
        exam = conn.execute("SELECT * FROM mock_exams WHERE id=? AND project_id=?", (exam_id, project_id)).fetchone()
        if not exam: abort(404)
        ids = json.loads(exam["question_ids_json"])
        questions_list = [get_question(conn, qid, project_id) for qid in ids]
        if exam["submitted_at"]:
            answers = json.loads(exam["answers_json"])
            details = []
            for q in questions_list:
                submitted = answers.get(str(q["id"]), [])
                details.append({"question": q, "submitted": submitted, "correct": submitted == q["answer"]})
            return render_template("mock_result.html", exam=exam, details=details)
        if request.method == "POST":
            answers, correct = {}, 0
            for q in questions_list:
                key = f"q_{q['id']}"
                submitted = sorted(request.form.getlist(key)) if q["type"] == "multiple" else [request.form.get(key, "")]
                answers[str(q["id"])] = submitted
                ok = submitted == q["answer"]
                correct += int(ok)
                record_attempt(conn, q, "mock", is_correct=ok, session_id=exam_id)
            score = round(100 * correct / len(questions_list), 1) if questions_list else 0
            conn.execute("UPDATE mock_exams SET submitted_at=?,answers_json=?,score=? WHERE id=?", (now_iso(), json.dumps(answers, ensure_ascii=False), score, exam_id))
            return redirect(url_for("mock_exam", exam_id=exam_id))
        return render_template("mock_exam.html", exam=exam, questions=questions_list)


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


def export_job_for_browser(conn, job_id):
    project_id = current_project(conn)["id"]
    job = conn.execute("SELECT * FROM export_jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
    if not job:
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


@app.route("/imports", methods=["GET", "POST"])
def imports_center():
    cleanup_expired_exports(DB_PATH, DATA_DIR)
    with db() as conn:
        project = current_project(conn); project_id = project["id"]
        if request.method == "POST":
            uploaded = request.files.get("file")
            if not uploaded or not uploaded.filename:
                flash("请选择要导入的文件", "error")
                return redirect(url_for("imports_center"))
            max_bytes = int(settings(conn).get("max_import_mb", "50")) * 1024 * 1024
            uploaded.stream.seek(0, 2); size = uploaded.stream.tell(); uploaded.stream.seek(0)
            if size > max_bytes:
                flash(f"文件超过 {max_bytes // 1024 // 1024}MB 上限", "error")
                return redirect(url_for("imports_center"))
            original_name = Path(uploaded.filename).name
            ext = Path(original_name).suffix.lower().lstrip(".")
            document_id = uuid.uuid4().hex
            job_id = uuid.uuid4().hex
            originals = DATA_DIR / "imports" / "originals"
            originals.mkdir(parents=True, exist_ok=True)
            safe_name = secure_filename(original_name) or f"document.{ext or 'bin'}"
            stored_relative = Path("imports") / "originals" / f"{document_id}_{safe_name}"
            stored_path = DATA_DIR / stored_relative
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
                import_queue.submit_detect(job_id)
            flash("原文件已保存，正在探测格式" if status == "queued" else message, "success" if status == "queued" else "error")
            return redirect(url_for("import_detail", job_id=job_id))
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


@app.get("/imports/<job_id>")
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


@app.get("/api/imports/<job_id>")
def import_status_api(job_id):
    with db() as conn:
        job = import_job_for_browser(conn, job_id)
        return {key: job[key] for key in ("id", "status", "stage", "progress", "message", "candidate_count",
          "valid_count", "duplicate_count", "committed_count", "updated_at", "completed_at")}


@app.post("/imports/<job_id>/target")
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
            return redirect(url_for("import_detail", job_id=job_id))
        subject_id = request.form.get("subject_id", type=int)
        if request.form.get("new_subject_name", "").strip():
            try:
                subject_id = conn.execute("INSERT INTO subjects(project_id,name,code) VALUES (?,?,?)",
                    (project_id, request.form["new_subject_name"].strip(), request.form.get("new_subject_code", "").strip())).lastrowid
            except sqlite3.IntegrityError:
                flash("同一项目内科目代码不能重复", "error")
                return redirect(url_for("import_detail", job_id=job_id))
        subject = conn.execute("SELECT * FROM subjects WHERE id=? AND project_id=?", (subject_id, project_id)).fetchone()
        if not subject:
            flash("请选择已有科目，或填写新科目名称", "error")
            return redirect(url_for("import_detail", job_id=job_id))
        now = now_iso()
        conn.execute("UPDATE source_documents SET project_id=?,subject_id=? WHERE id=?", (project_id, subject_id, job["source_document_id"]))
        conn.execute("""UPDATE import_jobs SET project_id=?,subject_id=?,status='queued',stage='parsing',progress=25,
          message='等待解析',updated_at=? WHERE id=?""", (project_id, subject_id, now, job_id))
        conn.commit()
        session["current_project_id"] = project_id
        import_queue.submit_parse(job_id)
    flash("目标科目已确认，正在完整解析", "success")
    return redirect(url_for("import_detail", job_id=job_id))


@app.post("/imports/<job_id>/mapping")
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
                return redirect(url_for("import_detail", job_id=job_id))
            if value:
                if value in used or not conn.execute(
                    "SELECT 1 FROM subjects WHERE id=? AND project_id=?", (value, job["project_id"])
                ).fetchone():
                    flash("不同来源科目不能映射到同一个目标科目", "error")
                    return redirect(url_for("import_detail", job_id=job_id))
                used.add(value)
            result[source["key"]] = {"target_subject_id": value, "name": source["name"], "code": source.get("code", "")}
        conn.execute("""UPDATE import_jobs SET mapping_json=?,status='queued',stage='parsing',progress=30,
          message='等待解析分享包',updated_at=? WHERE id=?""",
          (json.dumps({"subjects": result}, ensure_ascii=False), now_iso(), job_id))
        conn.commit()
        import_queue.submit_parse(job_id)
    flash("科目映射已确认，正在解析分享包", "success")
    return redirect(url_for("import_detail", job_id=job_id))


@app.post("/imports/<job_id>/retry")
def import_retry(job_id):
    with db() as conn:
        job = import_job_for_browser(conn, job_id)
        package_ready = job["import_kind"] == "package" and bool(json.loads(job["mapping_json"] or "{}").get("subjects"))
        conn.execute("UPDATE import_jobs SET status='queued',progress=?,message='等待重试',updated_at=? WHERE id=?",
                     (25 if job["subject_id"] or package_ready else 0, now_iso(), job_id))
        conn.commit()
        if job["subject_id"] or package_ready:
            import_queue.submit_parse(job_id)
        else:
            import_queue.submit_detect(job_id)
    flash("已从保存的原文件重新排队", "success")
    return redirect(url_for("import_detail", job_id=job_id))


@app.post("/imports/<job_id>/commit")
def import_commit(job_id):
    with db() as conn:
        job = import_job_for_browser(conn, job_id)
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
                DATA_DIR,
            )
        except ValueError as exc:
            conn.rollback()
            flash(str(exc), "error")
            return redirect(url_for("import_detail", job_id=job_id))
    flash(f"导入完成：{result['message']}", "success")
    return redirect(url_for("import_detail", job_id=job_id))


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


@app.post("/exports")
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
            return redirect(url_for("imports_center", tab="export"))
        if not count:
            flash("当前范围没有可导出的已核验题或草稿题", "error")
            return redirect(url_for("imports_center", tab="export"))
        job_id = uuid.uuid4().hex
        now = now_iso()
        conn.execute("""INSERT INTO export_jobs(id,project_id,scope_type,scope_id,include_drafts,status,stage,
          progress,message,question_count,created_at,updated_at) VALUES (?,?,?,?,?,'queued','queued',0,?,?,?,?)""",
          (job_id, project_id, scope_type, scope_id, include_drafts, "等待生成分享包", count, now, now))
        conn.commit()
        export_queue.submit(job_id)
    flash(f"已创建导出任务，预计包含 {count} 道题", "success")
    return redirect(url_for("imports_center", tab="export"))


@app.get("/api/exports/count")
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


@app.get("/api/exports/<job_id>")
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
    root = (DATA_DIR / "exports").resolve()
    path = (DATA_DIR / job["stored_path"]).resolve()
    return path if path.parent == root else None


@app.get("/exports/<job_id>/download")
def export_download(job_id):
    with db() as conn:
        job = export_job_for_browser(conn, job_id)
        if job["status"] != "completed" or (job["expires_at"] and job["expires_at"] <= now_iso()):
            abort(404)
        path = _export_file_path(job)
        if not path or not path.is_file():
            abort(404)
        return send_file(path, mimetype="application/zip", as_attachment=True, download_name=job["filename"])


@app.post("/exports/<job_id>/retry")
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
        export_queue.submit(job_id)
    flash("导出任务已重新排队", "success")
    return redirect(url_for("imports_center", tab="export"))


@app.post("/exports/<job_id>/delete")
def export_delete(job_id):
    with db() as conn:
        job = export_job_for_browser(conn, job_id)
        if job["status"] in {"queued", "running"}:
            flash("正在生成的任务不能删除，请等待完成或中断", "error")
            return redirect(url_for("imports_center", tab="export"))
        path = _export_file_path(job)
        if path:
            path.unlink(missing_ok=True)
        conn.execute("DELETE FROM export_jobs WHERE id=?", (job_id,))
    flash("分享包和任务记录已删除，题库内容不受影响", "success")
    return redirect(url_for("imports_center", tab="export"))


@app.get("/imports/template/<file_type>")
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


@app.route("/settings", methods=["GET", "POST"])
def app_settings():
    with db() as conn:
        project = current_project(conn); project_id = project["id"]
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
                if len(intervals) != 4 or any(x < 1 for x in intervals): raise ValueError
            except (ValueError, KeyError):
                flash("设置格式不正确：复习间隔必须是 4 个逗号分隔的正整数", "error")
            else:
                for key, value in project_values.items():
                    conn.execute("""INSERT INTO project_settings(project_id,key,value) VALUES (?,?,?)
                      ON CONFLICT(project_id,key) DO UPDATE SET value=excluded.value""", (project_id, key, value))
                for key, value in global_values.items():
                    conn.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
                app.config["MAX_CONTENT_LENGTH"] = int(global_values["max_import_mb"]) * 1024 * 1024
                export_queue.max_bytes = app.config["MAX_CONTENT_LENGTH"]
                flash("设置已保存", "success")
                return redirect(url_for("app_settings"))
        cfg = project_settings(conn, project_id) | settings(conn)
        return render_template("settings.html", cfg=cfg, project=project, modules=project_modules(conn, project_id),
                               db_path=str(DB_PATH), data_dir=str(DATA_DIR), lan_url=lan_url())


init_db()
with db() as _startup_conn:
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("STUDY_MAX_UPLOAD_MB") or settings(_startup_conn).get("max_import_mb", "50")) * 1024 * 1024
import_queue = ImportQueue(DB_PATH, DATA_DIR)
import_queue.recover()
export_queue = ExportQueue(DB_PATH, DATA_DIR, app.config["MAX_CONTENT_LENGTH"])
export_queue.recover()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
