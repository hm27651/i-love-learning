from __future__ import annotations

from flask import render_template, session

from migrations import PROJECT_SETTING_DEFAULTS, create_project


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


def settings(conn):
    return {row["key"]: row["value"] for row in conn.execute("SELECT key,value FROM settings")}


def project_settings(conn, project_id):
    values = dict(PROJECT_SETTING_DEFAULTS)
    values.update(
        {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key,value FROM project_settings WHERE project_id=?", (project_id,))
        }
    )
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
        row = conn.execute("SELECT * FROM learning_projects WHERE status='active' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM learning_projects ORDER BY id LIMIT 1").fetchone()
    if row is None:
        project_id = create_project(conn, "我的学习项目", "practice")
        row = conn.execute("SELECT * FROM learning_projects WHERE id=?", (project_id,)).fetchone()
    session["current_project_id"] = row["id"]
    return row


def project_modules(conn, project_id):
    return {
        row["module_key"]: bool(row["enabled"])
        for row in conn.execute("SELECT module_key,enabled FROM project_modules WHERE project_id=?", (project_id,))
    }


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
            week,
            f"第 {week} 周学习目标",
            "按当前项目安排章节学习",
            0,
            0,
            "",
        )
        conn.execute(
            """INSERT OR IGNORE INTO weekly_plans(project_id,week_no,title,chapter_goal,question_goal,lab_goal,notes)
               VALUES (?,?,?,?,?,?,?)""",
            (project_id, *source),
        )


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
