from __future__ import annotations

from datetime import date, timedelta

from flask import render_template

from services.core.common_service import json_object
from services.core.project_service import current_project
from services.core.session_service import active_mock_session, session_progress
from services.core.stats_service import active_week, attempt_trend, chapter_stats, learning_streak, readiness, subject_stats


def register_dashboard_routes(app, db_provider):
    @app.get("/", endpoint="dashboard")
    def dashboard():
        with db_provider() as conn:
            project = current_project(conn)
            project_id = project["id"]
            due = conn.execute(
                """SELECT COUNT(*) n FROM question_progress p JOIN questions q ON q.id=p.question_id
                  JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
                  JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND q.status='verified' AND p.due_date<=?""",
                (project_id, date.today().isoformat()),
            ).fetchone()["n"]
            errors = conn.execute(
                """SELECT COUNT(*) n FROM question_progress p JOIN questions q ON q.id=p.question_id
                  JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
                  JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND p.error_count>0""",
                (project_id,),
            ).fetchone()["n"]
            mocks = conn.execute(
                "SELECT * FROM mock_exams WHERE project_id=? AND submitted_at IS NOT NULL ORDER BY submitted_at DESC LIMIT 5",
                (project_id,),
            ).fetchall()
            sessions = conn.execute(
                """SELECT * FROM practice_sessions WHERE project_id=?
                  AND status IN ('active','paused') ORDER BY started_at DESC""",
                (project_id,),
            ).fetchall()
            continue_session = sessions[0] if sessions else None
            active_tasks = []
            for item in sessions:
                progress_info = session_progress(item)
                active_tasks.append(
                    {
                        "id": item["id"],
                        "kind": item["mode"],
                        "status": item["status"],
                        "done": progress_info["done"],
                        "total": progress_info["total"],
                    }
                )
            active_mock = active_mock_session(conn, project_id)
            if active_mock:
                active_tasks.append(
                    {
                        "id": active_mock["id"],
                        "kind": "mock",
                        "status": active_mock["status"],
                        "done": len([value for value in json_object(active_mock["answers_json"]).values() if value]),
                        "total": active_mock["objective_count"],
                    }
                )
            last_seven = conn.execute(
                """SELECT COUNT(*) n FROM attempts a JOIN questions q ON q.id=a.question_id
                  JOIN knowledge_points kp ON kp.id=q.knowledge_point_id JOIN chapters c ON c.id=kp.chapter_id
                  JOIN subjects s ON s.id=c.subject_id WHERE s.project_id=? AND a.answered_at>=?""",
                (project_id, (date.today() - timedelta(days=6)).isoformat()),
            ).fetchone()["n"]
            return render_template(
                "dashboard.html",
                chapters=chapter_stats(conn, project_id),
                due=due,
                errors=errors,
                mocks=mocks,
                readiness=readiness(conn, project_id),
                week=active_week(conn, project),
                streak=learning_streak(conn, project_id),
                last_seven=last_seven,
                continue_session=continue_session,
                active_tasks=active_tasks,
                trend=attempt_trend(conn, project_id, 14),
                project=project,
            )

    @app.get("/progress", endpoint="progress")
    def progress():
        with db_provider() as conn:
            project = current_project(conn)
            project_id = project["id"]
            return render_template(
                "progress.html",
                chapters=chapter_stats(conn, project_id),
                subjects=subject_stats(conn, project_id),
                mocks=conn.execute(
                    "SELECT * FROM mock_exams WHERE project_id=? AND submitted_at IS NOT NULL ORDER BY submitted_at DESC LIMIT 12",
                    (project_id,),
                ).fetchall(),
                readiness=readiness(conn, project_id),
                trend=attempt_trend(conn, project_id, 30),
                streak=learning_streak(conn, project_id),
                week=active_week(conn, project),
                project=project,
            )
