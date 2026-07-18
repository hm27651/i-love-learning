import unittest
import tempfile
import json
import sqlite3
from pathlib import Path
from unittest import mock

import portable_launcher


ROOT = Path(__file__).resolve().parents[1]


class PortableConfigTests(unittest.TestCase):
    def test_launcher_defaults_to_sibling_data_directory(self):
        root = portable_launcher.portable_root()
        self.assertEqual(portable_launcher.data_dir(root), root / "data")
        self.assertEqual(portable_launcher.launcher_config_path(root), root / ".portable-launcher.json")
        self.assertEqual(portable_launcher.local_url(), "http://127.0.0.1:23456")

    def test_source_default_data_root_is_repository_root(self):
        from services.core.storage_service import BASE_DIR

        self.assertEqual(BASE_DIR, ROOT)

    def test_pyinstaller_spec_uses_onedir_and_bundles_web_assets(self):
        content = (ROOT / "packaging" / "windows" / "I-Love-Learning.spec").read_text(encoding="utf-8")
        self.assertIn('name="I-Love-Learning-Portable"', content)
        self.assertIn('console=False', content)
        self.assertIn('(str(ROOT / "templates"), "templates")', content)
        self.assertIn('(str(ROOT / "static"), "static")', content)
        self.assertIn('"webview"', content)
        self.assertIn('"webview.platforms.edgechromium"', content)
        self.assertNotIn("onefile=True", content)

    def test_windows_build_script_creates_empty_data_directory(self):
        content = (ROOT / "tools" / "release" / "windows" / "build_portable_windows.ps1").read_text(encoding="utf-8")
        self.assertIn('dist\\I-Love-Learning-Portable', content)
        self.assertIn('I-Love-Learning-Portable.zip', content)
        self.assertIn('Join-Path $PackageDir "data"', content)
        self.assertIn('build\\portable-staging', content)
        self.assertIn("packaging\\windows\\requirements-portable.txt", content)
        self.assertIn("--distpath $StagingRoot packaging\\windows\\I-Love-Learning.spec", content)
        self.assertNotIn('Join-Path $Root "dist\\I-Love-Learning-Portable"', content)
        self.assertIn("PyInstaller failed", content)

    def test_portable_dependencies_include_gui_shell_only_for_windows_package(self):
        portable_requirements = (ROOT / "packaging" / "windows" / "requirements-portable.txt").read_text(encoding="utf-8")
        base_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("pywebview", portable_requirements)
        self.assertNotIn("pywebview", base_requirements)

    def test_launcher_config_requires_first_open_mode_then_persists_choice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_state = portable_launcher.load_launcher_config(root)
            self.assertTrue(first_state["local_only"])
            self.assertIsNone(first_state["open_mode"])
            self.assertEqual(Path(first_state["data_path"]), root / "data")

            selected = root / "shared-data"
            portable_launcher.save_launcher_config(
                root,
                local_only=False,
                open_mode="web",
                selected_data_dir=selected,
            )
            saved_state = portable_launcher.load_launcher_config(root)
            self.assertFalse(saved_state["local_only"])
            self.assertEqual(saved_state["open_mode"], "web")
            self.assertEqual(Path(saved_state["data_path"]), selected)
            self.assertTrue(portable_launcher.launcher_config_path(root).is_file())

    def test_legacy_launcher_config_is_read_then_saved_outside_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = portable_launcher.legacy_launcher_config_path(root)
            legacy.parent.mkdir(parents=True)
            legacy.write_text(json.dumps({"local_only": False, "open_mode": "gui"}), encoding="utf-8")
            loaded = portable_launcher.load_launcher_config(root)
            self.assertTrue(loaded["legacy_config"])
            self.assertEqual(loaded["open_mode"], "gui")
            portable_launcher.save_launcher_config(
                root,
                local_only=False,
                open_mode="gui",
                selected_data_dir=root / "data",
            )
            self.assertTrue(portable_launcher.launcher_config_path(root).is_file())

    def test_launcher_page_waits_for_pywebview_api_before_starting(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            portable_launcher, "portable_root", return_value=Path(temp_dir)
        ):
            launcher = portable_launcher.Launcher()
            try:
                html = launcher._launch_html()
            finally:
                portable_launcher.close_launcher_logging(Path(temp_dir))
        self.assertIn("pywebviewready", html)
        self.assertIn("apiReady", html)
        self.assertIn("waitForApi", html)
        self.assertIn("正在连接启动器", html)
        self.assertIn("startService", html)
        self.assertIn("openDataDir", html)
        self.assertIn("openLogsDir", html)
        self.assertIn("selectDataDir", html)
        self.assertIn("dataPathInput", html)
        self.assertIn("requiresMigration", html)
        self.assertIn("versionInfo", html)
        self.assertIn("启动器接口调用失败", html)
        self.assertIn("重新载入接口", html)
        self.assertIn("window.location.reload()", html)
        self.assertIn("document.querySelector('#startButton').disabled = !selected('openMode')", html)

    def test_launcher_exposes_only_explicit_api_allowlist(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.object(
            portable_launcher, "portable_root", return_value=Path(temp_dir)
        ):
            launcher = portable_launcher.Launcher()
            try:
                callables = launcher.exposed_api_callables()
                self.assertEqual(tuple(item.__name__ for item in callables), portable_launcher.EXPOSED_API_METHODS)
                self.assertNotIn("launcher", vars(launcher.api))
                self.assertEqual(set(vars(launcher.api)), {"_launcher"})
            finally:
                portable_launcher.close_launcher_logging(Path(temp_dir))

    def test_launcher_log_writes_to_data_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            try:
                portable_launcher.write_launcher_log(root, "hello")
                log_path = portable_launcher.data_dir(root) / "logs" / "launcher.log"
                self.assertTrue(log_path.exists())
                self.assertIn("hello", log_path.read_text(encoding="utf-8"))
                logger = portable_launcher.configure_launcher_logging(root)
                handler = next(item for item in logger.handlers if hasattr(item, "maxBytes"))
                self.assertEqual(handler.maxBytes, 1024 * 1024)
                self.assertEqual(handler.backupCount, 5)
            finally:
                portable_launcher.close_launcher_logging(root)

    def test_data_directory_migration_preserves_database_and_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            connection = sqlite3.connect(source / "h3cse.db")
            connection.executescript("""
              CREATE TABLE learning_projects(id INTEGER PRIMARY KEY);
              CREATE TABLE subjects(id INTEGER PRIMARY KEY);
              CREATE TABLE questions(id INTEGER PRIMARY KEY);
              CREATE TABLE attempts(id INTEGER PRIMARY KEY);
              INSERT INTO learning_projects(id) VALUES (1);
              INSERT INTO subjects(id) VALUES (1);
              INSERT INTO questions(id) VALUES (1);
            """)
            connection.commit()
            connection.close()
            (source / "uploads").mkdir()
            (source / "uploads" / "image.png").write_bytes(b"image")
            result = portable_launcher.migrate_data_directory(source, target)
            self.assertEqual(result["integrity"], "ok")
            self.assertEqual(result["questions"], 1)
            self.assertTrue((target / "uploads" / "image.png").is_file())
            self.assertTrue((source / "h3cse.db").is_file())

    def test_data_directory_migration_refuses_nonempty_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            sqlite3.connect(source / "h3cse.db").close()
            (target / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "目标目录不是空目录"):
                portable_launcher.migrate_data_directory(source, target)

    def test_data_directory_migration_refuses_nested_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source"
            source.mkdir()
            sqlite3.connect(source / "h3cse.db").close()
            with self.assertRaisesRegex(ValueError, "不能互相嵌套"):
                portable_launcher.migrate_data_directory(source, source / "nested")

    def test_version_metadata_matches_application_version(self):
        from version_info import APP_VERSION

        metadata = json.loads((ROOT / "packaging" / "windows" / "version.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["version"], APP_VERSION)

    def test_portable_readme_preserves_data_on_upgrade(self):
        content = (ROOT / "packaging" / "windows" / "README.txt").read_text(encoding="utf-8")
        self.assertIn("不要覆盖或删除 data", content)
        self.assertIn("ZIP 导入与导出", content)
        self.assertIn("不要把端口映射到公网", content)
        self.assertIn("软件内打开（GUI）", content)

    def test_readme_keeps_windows_entrypoint_portable_only(self):
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("双击 `I-Love-Learning.exe`", content)
        self.assertIn("软件内打开（GUI）", content)
        self.assertIn("源码方式只用于开发调试", content)
        self.assertNotIn("start" + ".bat", content)


if __name__ == "__main__":
    unittest.main()
