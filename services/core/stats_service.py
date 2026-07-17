from __future__ import annotations

from datetime import date, timedelta

from services.core.project_service import project_modules, project_settings


def chapter_stats(conn, project_id):
    rows = conn.execute(
        """
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
    """,
        (project_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["coverage"] = round(100 * row["attempted_count"] / row["verified_count"]) if row["verified_count"] else 0
        item["mastery"] = round(25 * row["avg_level"]) if row["verified_count"] else 0
        item["accuracy"] = round(100 * row["recent_correct"] / row["recent_total"]) if row["recent_total"] else 0
        result.append(item)
    return result


def readiness(conn, project_id):
    cfg = project_settings(conn, project_id)
    modules = project_modules(conn, project_id)
    scores = [
        row["score"]
        for row in conn.execute(
            """SELECT score FROM mock_exams WHERE project_id=? AND qualifying=1 AND submitted_at IS NOT NULL
               ORDER BY submitted_at DESC LIMIT 3""",
            (project_id,),
        )
    ]
    mock_ok = len(scores) == 3 and all(score >= float(cfg["mock_threshold"]) for score in scores)
    core = [chapter for chapter in chapter_stats(conn, project_id) if chapter["is_core"]]
    chapter_ok = bool(core) and all(chapter["mastery"] >= float(cfg["chapter_threshold"]) for chapter in core)
    lab = conn.execute(
        "SELECT COUNT(*) total,SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) done FROM labs WHERE project_id=?",
        (project_id,),
    ).fetchone()
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
    return {
        "ready": any(active.values()) and all(gates.values()),
        "mock_ok": mock_ok,
        "chapter_ok": chapter_ok,
        "lab_ok": lab_ok,
        "scores": scores,
        "lab_rate": lab_rate,
        "settings": cfg,
        "gates": gates,
        "active_gates": active,
        "gate_count": sum(active[key] and gates[key] for key in active),
        "active_gate_count": sum(active.values()),
    }


def learning_streak(conn, project_id):
    days = {
        date.fromisoformat(row["day"])
        for row in conn.execute(
            """SELECT DISTINCT substr(a.answered_at,1,10) day FROM attempts a JOIN questions q ON q.id=a.question_id
               JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
               JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? ORDER BY day DESC""",
            (project_id,),
        )
        if row["day"]
    }
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
    raw = {
        row["day"]: dict(row)
        for row in conn.execute(
            """
      SELECT substr(answered_at,1,10) day,
        SUM(CASE WHEN is_correct=1 THEN 1 ELSE 0 END) correct,
        SUM(CASE WHEN is_correct=0 THEN 1 ELSE 0 END) wrong,
        COUNT(*) total
      FROM attempts a JOIN questions q ON q.id=a.question_id
      JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
      JOIN subjects s ON s.id=c.subject_id
      WHERE a.answered_at>=? AND s.project_id=? GROUP BY substr(a.answered_at,1,10)
    """,
            (start.isoformat(), project_id),
        )
    }
    result = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        item = raw.get(day.isoformat(), {})
        result.append(
            {
                "day": day.isoformat(),
                "label": day.strftime("%m/%d"),
                "correct": item.get("correct", 0) or 0,
                "wrong": item.get("wrong", 0) or 0,
                "total": item.get("total", 0) or 0,
            }
        )
    maximum = max((item["total"] for item in result), default=0) or 1
    for item in result:
        item["correct_height"] = round(100 * item["correct"] / maximum)
        item["wrong_height"] = round(100 * item["wrong"] / maximum)
    return result


def subject_stats(conn, project_id):
    rows = conn.execute(
        """
      SELECT s.id,s.name,s.code exam_code,COUNT(q.id) verified_count,
        COUNT(CASE WHEN p.attempts>0 THEN 1 END) attempted_count,
        COALESCE(AVG(CASE WHEN q.id IS NOT NULL THEN COALESCE(p.mastery_level,0) END),0) avg_level
      FROM subjects s LEFT JOIN chapters c ON c.subject_id=s.id
      LEFT JOIN knowledge_points kp ON kp.chapter_id=c.id
      LEFT JOIN questions q ON q.knowledge_point_id=kp.id AND q.status='verified'
      LEFT JOIN question_progress p ON p.question_id=q.id WHERE s.project_id=? GROUP BY s.id ORDER BY s.id
    """,
        (project_id,),
    ).fetchall()
    return [
        dict(row)
        | {
            "coverage": round(100 * row["attempted_count"] / row["verified_count"]) if row["verified_count"] else 0,
            "mastery": round(25 * row["avg_level"]) if row["verified_count"] else 0,
        }
        for row in rows
    ]


def active_week(conn, project):
    try:
        start = date.fromisoformat(project["start_date"])
    except ValueError:
        start = date.today()
    week_no = max(1, min(project["duration_weeks"], (date.today() - start).days // 7 + 1))
    plan = conn.execute("SELECT * FROM weekly_plans WHERE project_id=? AND week_no=?", (project["id"], week_no)).fetchone()
    week_start = start + timedelta(days=(week_no - 1) * 7)
    attempts = conn.execute(
        """SELECT COUNT(*) n FROM attempts a JOIN questions q ON q.id=a.question_id
           JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
           JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND a.answered_at>=?""",
        (project["id"], week_start.isoformat()),
    ).fetchone()["n"]
    labs = conn.execute(
        "SELECT COUNT(*) n FROM labs WHERE project_id=? AND status='completed' AND updated_at>=?",
        (project["id"], week_start.isoformat()),
    ).fetchone()["n"]
    return {
        "week_no": week_no,
        "plan": plan,
        "attempts": attempts,
        "labs": labs,
        "question_rate": min(100, round(100 * attempts / plan["question_goal"]))
        if plan and plan["question_goal"]
        else 0,
        "lab_rate": min(100, round(100 * labs / plan["lab_goal"])) if plan and plan["lab_goal"] else 0,
    }
