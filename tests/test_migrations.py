import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from migrations import migrate_database
from services.core.migration_service import migrate_with_snapshot


class MigrationTests(unittest.TestCase):
    def test_safe_migration_creates_verified_pre_upgrade_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data_dir = root / "data"
            backup_dir = root / "backups"
            data_dir.mkdir()
            db_path = data_dir / "h3cse.db"
            conn = sqlite3.connect(db_path)
            migrate_database(conn)
            point_id = conn.execute("SELECT id FROM knowledge_points LIMIT 1").fetchone()[0]
            conn.execute(
                """INSERT INTO questions(id,knowledge_point_id,type,stem,options_json,answer_json,
                  explanation,difficulty,status,created_at,updated_at)
                  VALUES (42,?,'single','snapshot question','[\"A\",\"B\"]','[\"A\"]','',2,
                  'verified','2026-01-01','2026-01-01')""",
                (point_id,),
            )
            for index in (
                "ix_chapters_subject",
                "ix_points_chapter",
                "ix_questions_point_status",
                "ix_attempts_question_time",
                "ix_attempts_session",
            ):
                conn.execute(f"DROP INDEX {index}")
            conn.execute("DELETE FROM schema_migrations WHERE version=4")
            conn.execute("INSERT OR IGNORE INTO schema_migrations(version,applied_at) VALUES (3,'2026-01-01')")
            conn.commit()
            try:
                result = migrate_with_snapshot(
                    conn,
                    data_dir=data_dir,
                    db_path=db_path,
                    backup_dir=backup_dir,
                )
                conn.commit()
                self.assertEqual(result["before"], 3)
                self.assertEqual(result["after"], 4)
                self.assertEqual(result["integrity"], "ok")
                self.assertEqual(result["foreign_key_errors"], 0)
                snapshot_db = Path(result["snapshot"]) / "h3cse.db"
                self.assertTrue(snapshot_db.is_file())
                snapshot = sqlite3.connect(snapshot_db)
                try:
                    self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertEqual(snapshot.execute("SELECT id FROM questions").fetchone()[0], 42)
                    self.assertEqual(snapshot.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 3)
                finally:
                    snapshot.close()
            finally:
                conn.close()

    def test_fresh_database_does_not_create_migration_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data_dir = root / "data"
            backup_dir = root / "backups"
            data_dir.mkdir()
            db_path = data_dir / "h3cse.db"
            conn = sqlite3.connect(db_path)
            result = migrate_with_snapshot(
                conn,
                data_dir=data_dir,
                db_path=db_path,
                backup_dir=backup_dir,
            )
            conn.commit()
            conn.close()
            self.assertEqual(result["before"], 0)
            self.assertIsNone(result["snapshot"])
            self.assertFalse(backup_dir.exists())

    def test_fresh_database_contains_no_user_content(self):
        conn = sqlite3.connect(":memory:")
        version = migrate_database(conn)
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("questions", "source_documents", "import_jobs", "export_jobs", "attempts", "question_progress")
        }
        structure = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("learning_projects", "subjects", "chapters", "knowledge_points")
        }
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        self.assertEqual(version, 4)
        self.assertEqual(counts, {key: 0 for key in counts})
        self.assertEqual(structure, {key: 1 for key in structure})
        self.assertEqual(integrity, "ok")

    def test_v3_keeps_only_latest_unfinished_session_paused(self):
        conn = sqlite3.connect(":memory:")
        migrate_database(conn)
        project_id = conn.execute("SELECT id FROM learning_projects LIMIT 1").fetchone()[0]
        conn.execute("DROP INDEX ux_practice_active_mode")
        conn.execute("DROP INDEX ux_mock_active_project")
        conn.execute("DELETE FROM schema_migrations WHERE version=3")
        for index in range(3):
            conn.execute("""INSERT INTO practice_sessions(id,project_id,mode,question_ids_json,started_at,status)
              VALUES (?,?,?,?,?,'active')""", (f"practice-{index}", project_id, "practice", "[]", f"2026-01-0{index+1} 10:00:00"))
        for index in range(2):
            conn.execute("""INSERT INTO mock_exams(id,project_id,started_at,question_ids_json,objective_count,
              time_limit,status) VALUES (?,?,?,?,?,?,'active')""",
              (f"mock-{index}", project_id, f"2026-01-0{index+1} 11:00:00", "[]", 50, 60))
        version = migrate_database(conn)
        practice = dict(conn.execute("SELECT id,status FROM practice_sessions"))
        mocks = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT id,status,remaining_seconds FROM mock_exams")}
        self.assertEqual(version, 4)
        self.assertEqual(practice["practice-2"], "paused")
        self.assertEqual({practice["practice-0"], practice["practice-1"]}, {"terminated"})
        self.assertEqual(mocks["mock-1"], ("paused", 3600))
        self.assertEqual(mocks["mock-0"][0], "terminated")
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        conn.close()

    def test_v3_database_copy_preserves_question_ids_and_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "v3.db"
            conn = sqlite3.connect(source)
            migrate_database(conn)
            project_id = conn.execute("SELECT id FROM learning_projects LIMIT 1").fetchone()[0]
            subject_id = conn.execute("SELECT id FROM subjects WHERE project_id=?", (project_id,)).fetchone()[0]
            chapter_id = conn.execute("SELECT id FROM chapters WHERE subject_id=?", (subject_id,)).fetchone()[0]
            point_id = conn.execute("SELECT id FROM knowledge_points WHERE chapter_id=?", (chapter_id,)).fetchone()[0]
            conn.execute("""INSERT INTO questions(id,knowledge_point_id,type,stem,options_json,answer_json,
              explanation,difficulty,status,created_at,updated_at) VALUES (42,?,'single','迁移测试题','[\"A\",\"B\"]',
              '[\"A\"]','解析',2,'verified','2026-01-01','2026-01-01')""", (point_id,))
            conn.execute("INSERT INTO question_progress VALUES (42,2,'2026-01-08',1,1,0,'2026-01-01')")
            conn.execute("INSERT INTO attempts(question_id,mode,is_correct,answered_at,session_id) VALUES (42,'practice',1,'2026-01-01','fixture')")
            for index in ("ix_chapters_subject", "ix_points_chapter", "ix_questions_point_status",
                          "ix_attempts_question_time", "ix_attempts_session"):
                conn.execute(f"DROP INDEX {index}")
            conn.execute("DELETE FROM schema_migrations WHERE version=4")
            conn.commit()
            conn.close()
            target = Path(folder) / "migration.db"
            shutil.copy2(source, target)
            conn = sqlite3.connect(target)
            before_ids = [row[0] for row in conn.execute("SELECT id FROM questions ORDER BY id")]
            before = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
                "questions", "attempts", "question_progress", "practice_sessions", "labs", "weekly_plans", "mock_exams", "subjects"
            )}
            before_project = conn.execute("SELECT name,project_type,practice_alias FROM learning_projects").fetchone()
            version = migrate_database(conn)
            conn.commit()
            after_ids = [row[0] for row in conn.execute("SELECT id FROM questions ORDER BY id")]
            after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in before}
            project = conn.execute("SELECT name,project_type,practice_alias FROM learning_projects").fetchone()
            foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            conn.close()
        self.assertEqual(version, 4)
        self.assertEqual(before, after)
        self.assertEqual(before_ids, after_ids)
        self.assertEqual(project, before_project)
        self.assertFalse(foreign_key_errors)


if __name__ == "__main__":
    unittest.main()
