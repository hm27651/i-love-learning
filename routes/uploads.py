from __future__ import annotations

from flask import abort, send_from_directory

from services.core.project_service import current_project
from services.core.storage_service import UPLOAD_DIR


def register_upload_routes(app, db_provider):
    @app.get("/uploads/<path:name>", endpoint="upload")
    def upload(name):
        with db_provider() as conn:
            project_id = current_project(conn)["id"]
            allowed = conn.execute(
                """SELECT 1 FROM questions q JOIN knowledge_points kp ON kp.id=q.knowledge_point_id
                  JOIN chapters c ON c.id=kp.chapter_id JOIN subjects s ON s.id=c.subject_id
                  WHERE q.image_path=? AND s.project_id=? UNION ALL
                  SELECT 1 FROM labs l WHERE l.image_path=? AND l.project_id=? LIMIT 1""",
                (name, project_id, name, project_id),
            ).fetchone()
            if not allowed:
                abort(404)
        return send_from_directory(UPLOAD_DIR, name)
