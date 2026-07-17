import unittest
from pathlib import Path

import portable_launcher


ROOT = Path(__file__).resolve().parents[1]


class PortableConfigTests(unittest.TestCase):
    def test_launcher_defaults_to_sibling_data_directory(self):
        root = portable_launcher.portable_root()
        self.assertEqual(portable_launcher.data_dir(root), root / "data")
        self.assertEqual(portable_launcher.local_url(), "http://127.0.0.1:23456")

    def test_pyinstaller_spec_uses_onedir_and_bundles_web_assets(self):
        content = (ROOT / "packaging" / "windows" / "I-Love-Learning.spec").read_text(encoding="utf-8")
        self.assertIn('name="I-Love-Learning-Portable"', content)
        self.assertIn('console=False', content)
        self.assertIn('(str(ROOT / "templates"), "templates")', content)
        self.assertIn('(str(ROOT / "static"), "static")', content)
        self.assertNotIn("onefile=True", content)

    def test_windows_build_script_creates_empty_data_directory(self):
        content = (ROOT / "tools" / "release" / "windows" / "build_portable_windows.ps1").read_text(encoding="utf-8")
        self.assertIn('dist\\I-Love-Learning-Portable', content)
        self.assertIn('I-Love-Learning-Portable.zip', content)
        self.assertIn('Join-Path $PackageDir "data"', content)
        self.assertIn("packaging\\windows\\requirements-portable.txt", content)
        self.assertIn("--noconfirm packaging\\windows\\I-Love-Learning.spec", content)
        self.assertIn("PyInstaller failed", content)

    def test_portable_readme_preserves_data_on_upgrade(self):
        content = (ROOT / "packaging" / "windows" / "README.txt").read_text(encoding="utf-8")
        self.assertIn("不要覆盖或删除 data", content)
        self.assertIn("ZIP 导入与导出", content)
        self.assertIn("不要把端口映射到公网", content)

    def test_readme_keeps_windows_entrypoint_portable_only(self):
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("双击 `I-Love-Learning.exe`", content)
        self.assertIn("源码方式只用于开发调试", content)
        self.assertNotIn("start" + ".bat", content)


if __name__ == "__main__":
    unittest.main()
