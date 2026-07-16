import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.check_public_repo import scan_repository


class PublicRepositoryCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.git("init")
        self.git("config", "user.name", "Safety Test")
        self.git("config", "user.email", "safety@example.invalid")
        (self.repo / ".gitignore").write_text("data/\n.env\n*.db\n*.pdf\n*.png\n", encoding="utf-8")
        (self.repo / "README.md").write_text("clean repository\n", encoding="utf-8")
        self.git("add", ".gitignore", "README.md")
        self.git("commit", "-m", "initial")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=False
        )
        if result.returncode:
            self.fail(result.stderr or result.stdout)
        return result.stdout

    def test_ignored_local_database_and_pdf_do_not_fail(self):
        data = self.repo / "data"
        data.mkdir()
        (data / "local.db").write_bytes(b"SQLite format 3\0local-only")
        (data / "source.pdf").write_bytes(b"%PDF-local-only")
        self.assertEqual(scan_repository(self.repo), [])

    def test_force_added_sensitive_files_fail_index_check(self):
        data = self.repo / "data"
        data.mkdir()
        files = {
            "data/leak.db": b"SQLite format 3\0",
            "bank.pdf": b"%PDF",
            "question.png": b"PNG",
            "portable.exe": b"MZ",
            "native.pyd": b"PYD",
            ".env": b"SECRET=value",
            "deploy/linux/.env": b"BIND_IP=192.168.2.10",
        }
        for relative, content in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.git("add", "-f", *files)
        findings = scan_repository(self.repo, include_history=False)
        self.assertEqual({item.path for item in findings}, set(files))
        self.assertTrue(all(item.source == "index" for item in findings))

    def test_container_configuration_is_safe_to_track(self):
        files = {
            "Dockerfile": "FROM python:3.11-slim-bookworm\n",
            ".dockerignore": "data\n*.db\n.env\n",
            "deploy/linux/compose.yaml": "services: {}\n",
            "deploy/linux/config.example.env": "BIND_IP=192.168.2.10\n",
        }
        for relative, content in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.git("add", *files)
        self.assertEqual(scan_repository(self.repo, include_history=False), [])

    def test_portable_build_configuration_is_safe_to_track(self):
        files = {
            "I-Love-Learning.spec": "COLLECT(name='I-Love-Learning-Portable')\n",
            "portable_launcher.py": "print('launcher')\n",
            "requirements-portable.txt": "-r requirements.txt\npyinstaller==6.14.2\n",
            "tools/build_portable_windows.ps1": "Write-Host portable\n",
            "packaging/windows/README.txt": "portable readme\n",
            "packaging/windows/version.json": "{}\n",
            ".github/workflows/windows-portable.yml": "name: portable\n",
        }
        for relative, content in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.git("add", *files)
        self.assertEqual(scan_repository(self.repo, include_history=False), [])

    def test_only_fixed_readme_screenshots_are_allowed(self):
        allowed = {
            "docs/assets/desktop.jpg": b"public desktop screenshot",
            "docs/assets/mobile.jpg": b"public mobile screenshot",
        }
        for relative, content in allowed.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.git("add", "-f", *allowed)
        self.assertEqual(scan_repository(self.repo, include_history=False), [])

        other = self.repo / "docs" / "assets" / "question.png"
        other.write_bytes(b"question image")
        self.git("add", "-f", "docs/assets/question.png")
        findings = scan_repository(self.repo, include_history=False)
        self.assertEqual([item.path for item in findings], ["docs/assets/question.png"])

    def test_deleted_sensitive_file_still_fails_history_check(self):
        leak = self.repo / "old-bank.pdf"
        leak.write_bytes(b"%PDF historical leak")
        self.git("add", "-f", "old-bank.pdf")
        self.git("commit", "-m", "add leak")
        self.git("rm", "old-bank.pdf")
        self.git("commit", "-m", "remove leak")
        findings = scan_repository(self.repo)
        history = [item for item in findings if item.path == "old-bank.pdf"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].source, "history")
        self.assertIsNotNone(history[0].commit)


if __name__ == "__main__":
    unittest.main()
