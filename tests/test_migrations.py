import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from migrations import migrate_database


class MigrationTests(unittest.TestCase):
    def test_real_database_copy_preserves_question_ids_and_counts(self):
        root = Path(__file__).resolve().parents[1]
        source = root / "backups" / "pre_multi_project_20260714_154500" / "data" / "h3cse.db"
        if not source.exists():
            self.skipTest("pre-migration database backup is not available")
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
