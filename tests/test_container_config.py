import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ContainerConfigTests(unittest.TestCase):
    def test_dockerfile_runs_one_non_root_waitress_process(self):
        content = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.11-slim-bookworm", content)
        self.assertIn("USER 10001:10001", content)
        self.assertIn("umask 077 && exec waitress-serve --listen=0.0.0.0:23456 app:app", content)
        self.assertNotIn("gunicorn", content.casefold())
        self.assertNotIn("COPY . ", content)
        self.assertNotIn("ADD ", content)

    def test_build_context_excludes_local_study_data(self):
        patterns = {
            line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        required = {
            ".git", ".venv", "data", "backups", "题库", ".env", "**/.env",
            "*.db", "*.db-wal", "*.db-shm", "*.pdf", "*.vce", "*.docx",
            "*.xlsx", "*.csv", "*.png", "*.jpg", "*.zip",
        }
        self.assertTrue(required <= patterns, required - patterns)

    def test_compose_requires_private_binding_and_hardened_runtime(self):
        content = (ROOT / "deploy" / "linux" / "compose.yaml").read_text(encoding="utf-8")
        for expected in (
            "${BIND_IP:?", "${DATA_PATH:?", "${BACKUP_PATH:?", "read_only: true",
            "no-new-privileges:true", "cap_drop:", "restart: unless-stopped",
            "create_host_path: false", "STUDY_DATA_DIR: /data", "STUDY_BACKUP_DIR: /backups",
            "http://127.0.0.1:23456/health",
        ):
            self.assertIn(expected, content)
        self.assertNotIn('"0.0.0.0:', content)

    def test_example_configuration_contains_no_secret(self):
        content = (ROOT / "deploy" / "linux" / "config.example.env").read_text(encoding="utf-8")
        self.assertIn("BIND_IP=", content)
        self.assertIn("DATA_PATH=/srv/i-love-learning/data", content)
        self.assertNotIn("STUDY_SECRET", content)
        self.assertNotIn("PASSWORD", content)


if __name__ == "__main__":
    unittest.main()
