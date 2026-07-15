import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from transfer_service import MANIFEST_COMMENT_PREFIX, inspect_share_package


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write_package(path: Path, *, version=1, question_patch=None, extra_files=None, valid_comment=True):
    question = {
        "item_key": "q1", "subject_key": "s1", "chapter_key": "c1", "point_key": "p1",
        "type": "single", "stem": "安全校验测试题", "options": ["甲", "乙"], "answer": ["A"],
        "explanation": "解析", "difficulty": 2, "status": "verified", "image_ref": "", "image_missing": False,
    }
    if question_patch:
        question.update(question_patch)
    questions = json_bytes([question])
    files = {"questions.json": hashlib.sha256(questions).hexdigest()}
    for name, content in (extra_files or {}).items():
        files[name] = hashlib.sha256(content).hexdigest()
    manifest = {
        "format": "i-love-learning-question-bank", "version": version, "created_at": "2026-01-01 00:00:00",
        "project": {"name": "测试项目", "type": "practice"}, "scope": {"type": "project", "label": "测试项目"},
        "include_drafts": False, "counts": {"questions": 1, "images": len(extra_files or {}), "missing_images": int(question["image_missing"])},
        "subjects": [{"key": "s1", "name": "测试科目", "code": "T", "chapters": [
            {"key": "c1", "name": "测试章节", "is_core": True, "points": [{"key": "p1", "name": "测试知识点"}]}
        ]}], "warnings": [], "files": files,
    }
    manifest_content = json_bytes(manifest)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = MANIFEST_COMMENT_PREFIX + hashlib.sha256(manifest_content).hexdigest().encode("ascii") if valid_comment else b"changed"
        archive.writestr("manifest.json", manifest_content)
        archive.writestr("questions.json", questions)
        for name, content in (extra_files or {}).items():
            archive.writestr(name, content)


class SharePackageSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_package_and_manifest_integrity(self):
        path = self.root / "valid.zip"
        write_package(path)
        manifest, questions = inspect_share_package(path)
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(len(questions), 1)
        changed = self.root / "changed.zip"
        write_package(changed, valid_comment=False)
        with self.assertRaisesRegex(ValueError, "完整性"):
            inspect_share_package(changed)

    def test_rejects_future_version_invalid_answer_and_damaged_json(self):
        future = self.root / "future.zip"
        write_package(future, version=2)
        with self.assertRaisesRegex(ValueError, "不支持"):
            inspect_share_package(future)
        bad_answer = self.root / "answer.zip"
        write_package(bad_answer, question_patch={"answer": ["Z"]})
        with self.assertRaisesRegex(ValueError, "答案"):
            inspect_share_package(bad_answer)
        damaged = self.root / "damaged.zip"
        with zipfile.ZipFile(damaged, "w") as archive:
            content = b"{}"
            archive.comment = MANIFEST_COMMENT_PREFIX + hashlib.sha256(content).hexdigest().encode("ascii")
            archive.writestr("manifest.json", content)
            archive.writestr("questions.json", b"not-json")
        with self.assertRaises(ValueError):
            inspect_share_package(damaged)

    def test_rejects_path_traversal_unknown_files_and_compression_bomb(self):
        traversal = self.root / "traversal.zip"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("../escape.txt", b"x")
        with self.assertRaisesRegex(ValueError, "不安全"):
            inspect_share_package(traversal)

        unknown = self.root / "unknown.zip"
        write_package(unknown, extra_files={"notes.txt": b"not allowed"})
        with self.assertRaisesRegex(ValueError, "未知文件"):
            inspect_share_package(unknown)

        bomb = self.root / "bomb.zip"
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("huge.txt", b"0" * 2_000_000)
        with self.assertRaisesRegex(ValueError, "压缩率"):
            inspect_share_package(bomb)


if __name__ == "__main__":
    unittest.main()
