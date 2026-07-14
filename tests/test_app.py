import importlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path


class StudyAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ["H3CSE_DATA_DIR"] = cls.temp.name
        cls.mod = importlib.import_module("app")
        cls.mod.app.config.update(TESTING=True)
        cls.mod.BACKUP_DIR = Path(cls.temp.name) / "backups"

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.client = self.mod.app.test_client()
        with self.mod.db() as conn:
            self.project_id = conn.execute("SELECT id FROM learning_projects ORDER BY id LIMIT 1").fetchone()["id"]
            conn.execute("UPDATE learning_projects SET project_type='practical_certification',practice_alias='HCL实验' WHERE id=?", (self.project_id,))
            conn.execute("UPDATE project_modules SET enabled=1 WHERE project_id=?", (self.project_id,))
            conn.execute("DELETE FROM attempts")
            conn.execute("DELETE FROM question_progress")
            conn.execute("DELETE FROM practice_sessions")
            conn.execute("DELETE FROM mock_exams")
            conn.execute("DELETE FROM import_candidates")
            conn.execute("DELETE FROM questions")
            conn.execute("DELETE FROM labs")
            conn.execute("DELETE FROM import_jobs")
            conn.execute("DELETE FROM source_documents")

    def wait_for_job(self, job_id, statuses, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.mod.db() as conn:
                row = conn.execute("SELECT * FROM import_jobs WHERE id=?", (job_id,)).fetchone()
                if row and row["status"] in statuses:
                    return row
            time.sleep(0.05)
        self.fail(f"job {job_id} did not reach {statuses}")

    def upload_csv_job(self, stem="通用导入测试题"):
        csv_data = f"题型,题干,选项A,选项B,选项C,选项D,选项E,选项F,选项G,选项H,答案,解析,章节,知识点,原题号\n单选,{stem},正确项,错误项,,,,,,,A,测试解析,导入章节,导入知识点,CSV-1\n"
        response = self.client.post("/imports", data={"file": (io.BytesIO(csv_data.encode("utf-8-sig")), "bank.csv")}, content_type="multipart/form-data")
        job_id = response.headers["Location"].rstrip("/").split("/")[-1]
        self.wait_for_job(job_id, {"waiting_target"})
        with self.mod.db() as conn:
            subject_id = conn.execute("SELECT id FROM subjects WHERE project_id=? ORDER BY id LIMIT 1", (self.project_id,)).fetchone()["id"]
        self.client.post(f"/imports/{job_id}/target", data={"project_id": self.project_id, "subject_id": subject_id})
        return job_id, self.wait_for_job(job_id, {"ready", "blocked", "failed"})

    def add_question(self, qtype="single", answer=None, status="verified"):
        with self.mod.db() as conn:
            point = conn.execute("SELECT id FROM knowledge_points LIMIT 1").fetchone()["id"]
            answers = answer or (["A", "C"] if qtype == "multiple" else (["参考答案"] if qtype in {"fill", "short"} else ["A"]))
            options = ["选项一", "选项二", "选项三"] if qtype in {"single", "multiple"} else []
            cur = conn.execute("""INSERT INTO questions(knowledge_point_id,type,stem,options_json,answer_json,explanation,difficulty,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (point, qtype, f"{qtype} 测试题", json.dumps(options, ensure_ascii=False),
                json.dumps(answers, ensure_ascii=False), "测试解析", 2, status, self.mod.now_iso(), self.mod.now_iso()))
            return cur.lastrowid

    def test_all_main_pages_render(self):
        self.add_question()
        paths = ["/", "/progress", "/knowledge", "/questions", "/questions/new", "/practice", "/review", "/labs", "/labs/new", "/plans", "/mock", "/settings"]
        for path in paths:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_question_creation_supports_multiple_choice(self):
        with self.mod.db() as conn:
            point = conn.execute("SELECT id FROM knowledge_points LIMIT 1").fetchone()["id"]
        response = self.client.post("/questions/new", data={
            "knowledge_point_id": point, "type": "multiple", "stem": "选择正确项",
            "options": "第一项\n第二项\n第三项", "answer": "C,A", "explanation": "解析",
            "difficulty": 3, "source": "官方资料", "version_note": "Comware 7", "status": "verified",
        })
        self.assertEqual(response.status_code, 302)
        with self.mod.db() as conn:
            row = conn.execute("SELECT * FROM questions").fetchone()
            self.assertEqual(json.loads(row["answer_json"]), ["A", "C"])
            self.assertEqual(row["status"], "verified")

    def test_question_list_is_paginated(self):
        for _ in range(55):
            self.add_question()
        first = self.client.get("/questions")
        second = self.client.get("/questions?page=2")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertIn("第 1 / 2 页".encode("utf-8"), first.data)
        self.assertIn("第 2 / 2 页".encode("utf-8"), second.data)
        self.assertEqual(first.data.count(b'name="page"'), 2)
        self.assertEqual(first.data.count("跳转".encode("utf-8")), 2)

    def test_question_bulk_status_and_review_workspace(self):
        first = self.add_question(status="draft")
        second = self.add_question(status="draft")
        review = self.client.get(f"/questions/review/{first}?status=draft&page=1")
        self.assertEqual(review.status_code, 200)
        self.assertIn("审核题目".encode("utf-8"), review.data)
        response = self.client.post("/questions/bulk", data={
            "action": "verified", "question_id": [str(first), str(second)],
            "return_to": "/questions?status=draft&page=2",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/questions?status=draft&page=2"))
        with self.mod.db() as conn:
            statuses = {row["status"] for row in conn.execute("SELECT status FROM questions WHERE id IN (?,?)", (first, second))}
            self.assertEqual(statuses, {"verified"})

    def test_question_bulk_can_move_within_project_only(self):
        question_id = self.add_question(status="draft")
        with self.mod.db() as conn:
            chapter = conn.execute("""SELECT c.id FROM chapters c JOIN subjects s ON s.id=c.subject_id
              WHERE s.project_id=? ORDER BY c.id LIMIT 1""", (self.project_id,)).fetchone()["id"]
            target = conn.execute("INSERT INTO knowledge_points(chapter_id,name) VALUES (?,?)", (chapter, "批量移动目标")).lastrowid
        response = self.client.post("/questions/bulk", data={"action": "move", "question_id": [str(question_id)], "target_point_id": target})
        self.assertEqual(response.status_code, 302)
        with self.mod.db() as conn:
            self.assertEqual(conn.execute("SELECT knowledge_point_id FROM questions WHERE id=?", (question_id,)).fetchone()["knowledge_point_id"], target)

    def test_objective_practice_updates_mastery_and_due_date(self):
        qid = self.add_question("single", ["A"])
        response = self.client.post("/practice", data={"count": 1})
        location = response.headers["Location"]
        self.assertEqual(self.client.get(location).status_code, 200)
        answer = self.client.post(location, data={"answer": "A"})
        self.assertIn("回答正确".encode("utf-8"), answer.data)
        with self.mod.db() as conn:
            progress = conn.execute("SELECT * FROM question_progress WHERE question_id=?", (qid,)).fetchone()
            self.assertEqual(progress["mastery_level"], 1)
            self.assertEqual(progress["correct_attempts"], 1)

    def test_practice_match_count_respects_filters_and_status(self):
        self.add_question("single", status="verified")
        self.add_question("multiple", status="verified")
        self.add_question("single", status="draft")
        self.add_question("single", status="archived")
        with self.mod.db() as conn:
            chapter_id = conn.execute("SELECT id FROM chapters LIMIT 1").fetchone()["id"]
        self.assertEqual(self.client.get("/api/practice/count").get_json()["count"], 2)
        self.assertEqual(self.client.get("/api/practice/count?type=single").get_json()["count"], 1)
        self.assertEqual(self.client.get(f"/api/practice/count?chapter_id={chapter_id}").get_json()["count"], 2)
        self.assertEqual(self.client.get("/api/practice/count?chapter_id=999999").get_json()["count"], 0)

    def test_all_practice_mode_includes_every_match_and_count_mode_stays_capped(self):
        for _ in range(105):
            self.add_question("single", status="verified")
        response = self.client.post("/practice", data={"selection_mode": "all", "count": 10})
        self.assertEqual(response.status_code, 302)
        with self.mod.db() as conn:
            session = conn.execute("SELECT * FROM practice_sessions ORDER BY started_at DESC LIMIT 1").fetchone()
            ids = json.loads(session["question_ids_json"])
            self.assertEqual(len(ids), 105)
            self.assertEqual(len(set(ids)), 105)
            conn.execute("DELETE FROM practice_sessions")
        self.client.post("/practice", data={"selection_mode": "count", "count": 500})
        with self.mod.db() as conn:
            session = conn.execute("SELECT * FROM practice_sessions LIMIT 1").fetchone()
            self.assertEqual(len(json.loads(session["question_ids_json"])), 100)

    def test_all_practice_mode_does_not_create_empty_session(self):
        response = self.client.post("/practice", data={"selection_mode": "all"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/practice"))
        with self.mod.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) n FROM practice_sessions").fetchone()["n"], 0)

    def test_wrong_answer_resets_mastery_and_counts_error(self):
        qid = self.add_question("single", ["A"])
        with self.mod.db() as conn:
            question = self.mod.get_question(conn, qid)
            self.mod.record_attempt(conn, question, "practice", is_correct=True)
            self.mod.record_attempt(conn, question, "practice", is_correct=False)
            progress = conn.execute("SELECT * FROM question_progress WHERE question_id=?", (qid,)).fetchone()
            self.assertEqual(progress["mastery_level"], 0)
            self.assertEqual(progress["error_count"], 1)

    def test_subjective_question_requires_self_rating(self):
        qid = self.add_question("short")
        with self.mod.db() as conn:
            sid = "subjective-test"
            conn.execute("INSERT INTO practice_sessions(id,project_id,mode,question_ids_json,started_at) VALUES (?,?,?,?,?)", (sid, self.project_id, "practice", json.dumps([qid]), self.mod.now_iso()))
        reveal = self.client.post(f"/practice/{sid}", data={"action": "reveal"})
        self.assertIn("请对照参考答案自评".encode("utf-8"), reveal.data)
        rated = self.client.post(f"/practice/{sid}", data={"rating": "fuzzy"})
        self.assertIn("本题已记录".encode("utf-8"), rated.data)
        with self.mod.db() as conn:
            attempt = conn.execute("SELECT * FROM attempts WHERE question_id=?", (qid,)).fetchone()
            self.assertEqual(attempt["self_rating"], "fuzzy")

    def test_mock_exam_is_snapshotted_and_scored(self):
        qid = self.add_question("single", ["A"])
        response = self.client.post("/mock", data={"count": 1, "minutes": 5})
        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        page = self.client.get(location)
        self.assertIn("模拟考试".encode("utf-8"), page.data)
        result = self.client.post(location, data={f"q_{qid}": "A"}, follow_redirects=True)
        self.assertIn(b"100.0%", result.data)
        with self.mod.db() as conn:
            exam = conn.execute("SELECT * FROM mock_exams").fetchone()
            self.assertEqual(exam["score"], 100.0)
            self.assertEqual(json.loads(exam["question_ids_json"]), [qid])

    def test_readiness_requires_all_three_gates(self):
        qid = self.add_question("single", ["A"])
        with self.mod.db() as conn:
            conn.execute("INSERT INTO question_progress(question_id,mastery_level,due_date,attempts,correct_attempts,error_count) VALUES (?,?,?,?,?,?)", (qid, 4, "2099-01-01", 4, 4, 0))
            chapter = conn.execute("SELECT c.id FROM chapters c JOIN knowledge_points kp ON kp.chapter_id=c.id JOIN questions q ON q.knowledge_point_id=kp.id WHERE q.id=?", (qid,)).fetchone()["id"]
            conn.execute("INSERT INTO labs(project_id,chapter_id,title,status,created_at,updated_at) VALUES (?,?,?,?,?,?)", (self.project_id, chapter, "达标实验", "completed", self.mod.now_iso(), self.mod.now_iso()))
            for n in range(3):
                conn.execute("INSERT INTO mock_exams(id,project_id,started_at,submitted_at,question_ids_json,score,objective_count,time_limit,qualifying) VALUES (?,?,?,?,?,?,?,?,1)", (f"ready-{n}", self.project_id, self.mod.now_iso(), self.mod.now_iso(), "[]", 90, 50, 60))
            state = self.mod.readiness(conn, self.project_id)
            self.assertTrue(state["ready"])

    def test_two_browser_clients_keep_independent_current_projects(self):
        with self.mod.db() as conn:
            other = self.mod.create_project(conn, "英语课程", "practice")
        first_client = self.mod.app.test_client()
        second_client = self.mod.app.test_client()
        first_client.post("/projects/switch", data={"project_id": self.project_id, "return_to": "/"})
        second_client.post("/projects/switch", data={"project_id": other, "return_to": "/"})
        self.assertIn("HCL实验".encode(), first_client.get("/").data)
        self.assertIn("英语课程".encode(), second_client.get("/").data)
        with first_client.session_transaction() as first_session, second_client.session_transaction() as second_session:
            self.assertEqual(first_session["current_project_id"], self.project_id)
            self.assertEqual(second_session["current_project_id"], other)

    def test_question_lists_and_direct_session_urls_are_project_isolated(self):
        first_question = self.add_question()
        with self.mod.db() as conn:
            other = self.mod.create_project(conn, "软考", "exam_prep")
            subject = conn.execute("INSERT INTO subjects(project_id,name,code) VALUES (?,?,?)", (other, "网络工程师", "RK-NET")).lastrowid
            chapter = conn.execute("INSERT INTO chapters(subject_id,name,is_core) VALUES (?,?,1)", (subject, "网络基础")).lastrowid
            point = conn.execute("INSERT INTO knowledge_points(chapter_id,name) VALUES (?,?)", (chapter, "OSI")).lastrowid
            other_question = conn.execute("""INSERT INTO questions(knowledge_point_id,type,stem,options_json,answer_json,
              status,created_at,updated_at) VALUES (?,?,?,?,?,'verified',?,?)""",
              (point, "single", "只属于软考项目", '["A","B"]', '["A"]', self.mod.now_iso(), self.mod.now_iso())).lastrowid
            sid = "other-project-session"
            conn.execute("INSERT INTO practice_sessions(id,project_id,mode,question_ids_json,started_at) VALUES (?,?,?,?,?)",
                         (sid, other, "practice", json.dumps([other_question]), self.mod.now_iso()))
        page = self.client.get("/questions")
        self.assertNotIn("只属于软考项目".encode(), page.data)
        self.assertEqual(self.client.get(f"/questions/{other_question}/edit").status_code, 404)
        self.assertEqual(self.client.get(f"/practice/{sid}").status_code, 404)
        self.assertEqual(self.client.get(f"/questions/{first_question}/edit").status_code, 200)

    def test_csv_import_uses_confirm_parse_commit_pipeline_and_creates_draft(self):
        job_id, job = self.upload_csv_job()
        self.assertEqual(job["status"], "ready")
        self.assertEqual(job["candidate_count"], 1)
        result = self.client.post(f"/imports/{job_id}/commit", data={"duplicate_action": "skip"})
        self.assertEqual(result.status_code, 302)
        with self.mod.db() as conn:
            question = conn.execute("SELECT * FROM questions WHERE stem='通用导入测试题'").fetchone()
            self.assertIsNotNone(question)
            self.assertEqual(question["status"], "draft")
            self.assertEqual(question["source_item_key"], "CSV-1")
            self.assertEqual(question["import_batch_id"], job_id)

    def test_duplicate_import_defaults_to_skip_and_can_keep_copy(self):
        first_id, first = self.upload_csv_job("重复题测试")
        self.assertEqual(first["status"], "ready")
        self.client.post(f"/imports/{first_id}/commit", data={"duplicate_action": "skip"})
        second_id, second = self.upload_csv_job("重复题测试")
        self.assertEqual(second["duplicate_count"], 1)
        self.client.post(f"/imports/{second_id}/commit", data={"duplicate_action": "skip"})
        with self.mod.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) n FROM questions WHERE stem='重复题测试'").fetchone()["n"], 1)
        third_id, third = self.upload_csv_job("重复题测试")
        self.assertEqual(third["duplicate_count"], 1)
        self.client.post(f"/imports/{third_id}/commit", data={"duplicate_action": "copy"})
        with self.mod.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) n FROM questions WHERE stem='重复题测试'").fetchone()["n"], 2)
        fourth_id, fourth = self.upload_csv_job("重复题测试")
        self.assertGreaterEqual(fourth["duplicate_count"], 1)
        self.client.post(f"/imports/{fourth_id}/commit", data={"duplicate_action": "update"})
        with self.mod.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) n FROM questions WHERE stem='重复题测试'").fetchone()["n"], 2)
            self.assertIsNotNone(conn.execute("SELECT 1 FROM questions WHERE stem='重复题测试' AND import_batch_id=?", (fourth_id,)).fetchone())

    def test_docx_and_xlsx_fixed_formats_parse(self):
        from docx import Document
        from openpyxl import Workbook
        from import_service import parse_document

        root = Path(self.temp.name)
        docx_path = root / "sample.docx"
        document = Document()
        for line in ("1. DOCX 测试题", "A. 正确", "B. 错误", "答案：A", "解析：DOCX 解析"):
            document.add_paragraph(line)
        document.save(docx_path)
        docx_records, docx_errors = parse_document(docx_path, "docx")
        self.assertEqual(len(docx_records), 1)
        self.assertFalse(docx_records[0]["validation_error"])
        self.assertFalse(docx_errors)

        xlsx_path = root / "sample.xlsx"
        workbook = Workbook(); sheet = workbook.active
        sheet.append(["题型", "题干", "选项A", "选项B", "答案", "解析", "章节", "知识点", "原题号"])
        sheet.append(["单选", "XLSX 测试题", "正确", "错误", "A", "解析", "章节", "知识点", "X-1"])
        workbook.save(xlsx_path); workbook.close()
        xlsx_records, xlsx_errors = parse_document(xlsx_path, "xlsx")
        self.assertEqual(len(xlsx_records), 1)
        self.assertFalse(xlsx_records[0]["validation_error"])
        self.assertFalse(xlsx_errors)

    def test_unknown_import_format_is_rejected_without_questions(self):
        response = self.client.post("/imports", data={"file": (io.BytesIO(b"legacy"), "legacy.doc")}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        job_id = response.headers["Location"].rstrip("/").split("/")[-1]
        with self.mod.db() as conn:
            job = conn.execute("SELECT * FROM import_jobs WHERE id=?", (job_id,)).fetchone()
            self.assertEqual(job["status"], "failed")
            self.assertEqual(conn.execute("SELECT COUNT(*) n FROM questions").fetchone()["n"], 0)

    def test_invalid_import_batch_never_partially_writes(self):
        csv_data = "题型,题干,选项A,选项B,答案,解析,章节,知识点,原题号\n单选,有效题,甲,乙,A,解析,章节,知识点,1\n单选,缺答案题,甲,乙,,解析,章节,知识点,2\n"
        response = self.client.post("/imports", data={"file": (io.BytesIO(csv_data.encode("utf-8-sig")), "invalid.csv")}, content_type="multipart/form-data")
        job_id = response.headers["Location"].rstrip("/").split("/")[-1]
        self.wait_for_job(job_id, {"waiting_target"})
        with self.mod.db() as conn:
            subject_id = conn.execute("SELECT id FROM subjects WHERE project_id=? ORDER BY id LIMIT 1", (self.project_id,)).fetchone()["id"]
        self.client.post(f"/imports/{job_id}/target", data={"project_id": self.project_id, "subject_id": subject_id})
        job = self.wait_for_job(job_id, {"blocked", "ready", "failed"})
        self.assertEqual(job["status"], "blocked")
        self.client.post(f"/imports/{job_id}/commit", data={"duplicate_action": "skip"})
        with self.mod.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) n FROM questions").fetchone()["n"], 0)

    def test_disabled_optional_module_has_clear_direct_access_page(self):
        with self.mod.db() as conn:
            other = self.mod.create_project(conn, "纯刷题项目", "practice")
        self.client.post("/projects/switch", data={"project_id": other, "return_to": "/"})
        page = self.client.get("/mock")
        self.assertEqual(page.status_code, 200)
        self.assertIn("当前项目未启用".encode(), page.data)
        self.assertNotIn("模拟考试</span>".encode(), self.client.get("/").data)

    def test_archive_restore_and_permanent_delete_create_backup(self):
        with self.mod.db() as conn:
            other = self.mod.create_project(conn, "可归档项目", "practice")
        self.client.post("/projects/switch", data={"project_id": other, "return_to": "/"})
        self.client.post(f"/projects/{other}/archive")
        with self.client.session_transaction() as browser_session:
            self.assertEqual(browser_session["current_project_id"], self.project_id)
        self.client.post(f"/projects/{other}/restore")
        self.client.post(f"/projects/{other}/archive")
        response = self.client.post(f"/projects/{other}/delete", data={"confirmation": "永久删除"})
        self.assertEqual(response.status_code, 302)
        with self.mod.db() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM learning_projects WHERE id=?", (other,)).fetchone())
        self.assertTrue(any(self.mod.BACKUP_DIR.glob(f"pre_delete_project_{other}_*/data/{self.mod.DB_PATH.name}")))


if __name__ == "__main__":
    unittest.main()
