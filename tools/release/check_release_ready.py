from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run(command: list[str], *, title: str, allow_fail: bool = False) -> int:
    print(f"\n== {title} ==")
    print(" ".join(command))
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode and not allow_fail:
        raise SystemExit(completed.returncode)
    return completed.returncode


def capture(command: list[str]) -> str:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode:
        return completed.stderr.strip() or completed.stdout.strip()
    return completed.stdout.strip()


def existing_paths() -> list[str]:
    paths = [
        ".github/workflows/public-repository-check.yml",
        ".github/workflows/windows-portable.yml",
        "tools/safety/check_public_repo.py",
        "tools/release/windows/build_portable_windows.ps1",
        "tools/release/windows/smoke_portable_windows.ps1",
        "packaging/windows/I-Love-Learning.spec",
    ]
    return [item for item in paths if not (ROOT / item).exists()]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="我爱学习发布前只读检查")
    parser.add_argument("--allow-dirty", action="store_true", help="允许存在未提交改动，仅用于开发中自测")
    parser.add_argument("--skip-tests", action="store_true", help="跳过自动测试，仅做快速检查")
    args = parser.parse_args(argv)

    branch = capture(["git", "branch", "--show-current"]) or "未知分支"
    status = capture(["git", "status", "--short"])
    missing = existing_paths()

    print("我爱学习发布前检查")
    print(f"仓库：{ROOT}")
    print(f"分支：{branch}")
    if status:
        print("\n工作区存在未提交改动：")
        print(status)
        if not args.allow_dirty:
            print("\n请先提交或暂存确认这些改动；如只是开发中自测，可添加 --allow-dirty。")
            return 2
    else:
        print("工作区：干净")

    if missing:
        print("\n缺少发布/安全配置：")
        for item in missing:
            print(f"- {item}")
        return 2

    run([sys.executable, "tools/safety/check_public_repo.py"], title="公开仓库安全检查")
    run(
        [
            sys.executable,
            "-m",
            "py_compile",
            "app.py",
            "app_runtime.py",
            "data_management.py",
            "migrations.py",
            "import_service.py",
            "knowledge_service.py",
            "transfer_service.py",
            "portable_launcher.py",
            "version_info.py",
            "services/core/common_service.py",
            "services/core/project_service.py",
            "services/core/session_service.py",
            "services/core/stats_service.py",
            "services/core/storage_service.py",
            "services/questions/question_service.py",
            "services/imports/import_service.py",
            "services/knowledge/knowledge_common.py",
            "services/knowledge/knowledge_duplicates.py",
            "services/knowledge/knowledge_delete_service.py",
            "services/transfer/transfer_common.py",
            "services/transfer/export_service.py",
            "services/transfer/share_package_service.py",
            "routes/projects.py",
            "routes/dashboard.py",
            "routes/exports.py",
            "routes/imports.py",
            "routes/knowledge.py",
            "routes/labs.py",
            "routes/mock.py",
            "routes/plans.py",
            "routes/practice.py",
            "routes/questions.py",
            "routes/settings.py",
            "routes/uploads.py",
        ],
        title="Python 语法检查",
    )
    if args.skip_tests:
        print("\n已按参数跳过自动测试。正式发布前仍建议运行完整测试。")
    else:
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], title="自动测试")

    print("\n检查完成。建议发布顺序：")
    print("1. git add 需要提交的源码、文档和配置")
    print("2. git commit -m \"合适的中文说明\"")
    print("3. git push")
    print("4. 等待 GitHub Actions 在干净环境构建 Portable")
    print("5. 从 Actions artifact 或 Release workflow 获取 ZIP 与 SHA256")
    print("\n本脚本不会提交、推送、创建 Release 或上传资产。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
