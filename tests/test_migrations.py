import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from migrations import migrate_database


class MigrationTests(unittest.TestCase):
    def test_fresh_database_contains_no_user_content(self):
        conn = sqlite3.connect(":memory:")
        version = migrate_database(conn)
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("questions", "source_documents", "import_jobs", "attempts", "question_progress")
        }
        structure = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("learning_projects", "subjects", "chapters", "knowledge_points")
        }
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        self.assertEqual(version, 1)
        self.assertEqual(counts, {key: 0 for key in counts})
        self.assertEqual(structure, {key: 1 for key in structure})
        self.assertEqual(integrity, "ok")

    def test_real_database_copy_preserves_question_ids_and_counts(self):
        source_value = os.environ.get("STUDY_LEGACY_DB_FIXTURE")
        if not source_value:
            self.skipTest("STUDY_LEGACY_DB_FIXTURE is not configured")
        source = Path(source_value)
        if not source.is_file():
            self.skipTest("configured legacy database fixture is not available")
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "migration.db"
            shutil.copy2(source, target)
            conn = sqlite3.connect(target)
            before_ids = [row[0] for row in conn.execute("SELECT id FROM questions ORDER BY id")]
            before = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
                "questions", "attempts", "question_progress", "practice_sessions", "labs", "weekly_plans", "mock_exams", "subjects"
            )}
            version = migrate_database(conn)
            conn.commit()
            after_ids = [row[0] for row in conn.execute("SELECT id FROM questions ORDER BY id")]
            after = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in before}
            project = conn.execute("SELECT name,project_type,practice_alias FROM learning_projects").fetchone()
            foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            conn.close()
        self.assertEqual(version, 1)
        self.assertEqual(before, after)
        self.assertEqual(before_ids, after_ids)
        self.assertEqual(project, ("H3CSE", "practical_certification", "HCL实验"))
        self.assertFalse(foreign_key_errors)


if __name__ == "__main__":
    unittest.main()
