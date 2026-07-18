from __future__ import annotations

import argparse
import html
import json
import logging
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import BOTH, DISABLED, NORMAL, BooleanVar, StringVar, Tk, filedialog, ttk, messagebox

from version_info import load_version_info


APP_NAME = "我爱学习"
DEFAULT_PORT = 23456
VALID_OPEN_MODES = {"gui", "web"}
EXPOSED_API_METHODS = (
    "get_state",
    "start_service",
    "stop_service",
    "open_browser",
    "open_data_dir",
    "open_logs_dir",
    "select_data_dir",
    "back_to_settings",
)


def portable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir(root: Path | None = None) -> Path:
    return (root or portable_root()) / "data"


def launcher_config_path(root: Path | None = None) -> Path:
    return (root or portable_root()) / ".portable-launcher.json"


def legacy_launcher_config_path(root: Path | None = None) -> Path:
    return data_dir(root) / ".portable-launcher.json"


def normalize_data_path(value: object, root: Path) -> Path:
    text = os.path.expandvars(str(value or "").strip())
    path = Path(text).expanduser() if text else data_dir(root)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def local_url(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def lan_ip() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def lan_url(port: int = DEFAULT_PORT) -> str:
    ip = lan_ip() or "本机局域网 IP"
    return f"http://{ip}:{port}"


def is_port_available(host: str, port: int = DEFAULT_PORT) -> bool:
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((probe_host, port)) != 0


def load_launcher_config(root: Path | None = None) -> dict[str, object]:
    root = root or portable_root()
    path = launcher_config_path(root)
    legacy_path = legacy_launcher_config_path(root)
    if not path.exists() and legacy_path.exists():
        path = legacy_path
    suggested_data = os.environ.get("STUDY_DATA_DIR") or data_dir(root)
    if not path.exists():
        return {
            "local_only": True,
            "open_mode": None,
            "data_path": str(normalize_data_path(suggested_data, root)),
            "legacy_config": False,
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    local_only = raw.get("local_only")
    open_mode = raw.get("open_mode")
    return {
        "local_only": local_only if isinstance(local_only, bool) else True,
        "open_mode": open_mode if open_mode in VALID_OPEN_MODES else None,
        "data_path": str(normalize_data_path(raw.get("data_path") or suggested_data, root)),
        "legacy_config": path == legacy_path,
    }


def save_launcher_config(root: Path, *, local_only: bool, open_mode: str, selected_data_dir: Path) -> None:
    if open_mode not in VALID_OPEN_MODES:
        raise ValueError(f"unsupported open mode: {open_mode}")
    path = launcher_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "local_only": local_only,
                "open_mode": open_mode,
                "data_path": str(selected_data_dir.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def launcher_log_path(root: Path | None = None, selected_data_dir: Path | None = None) -> Path:
    return (selected_data_dir or data_dir(root)) / "logs" / "launcher.log"


def configure_launcher_logging(root: Path, selected_data_dir: Path | None = None) -> logging.Logger:
    log_dir = selected_data_dir or data_dir(root)
    log_dir = log_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target = launcher_log_path(root, selected_data_dir).resolve()
    logger = logging.getLogger(f"i-love-learning.launcher.{target}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = next(
        (
            item
            for item in logger.handlers
            if isinstance(item, RotatingFileHandler) and Path(item.baseFilename) == target
        ),
        None,
    )
    if handler is None:
        handler = RotatingFileHandler(
            target,
            maxBytes=1024 * 1024,
            backupCount=5,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)

    pywebview_logger = logging.getLogger("pywebview")
    if not any(
        isinstance(item, RotatingFileHandler) and Path(item.baseFilename) == target
        for item in pywebview_logger.handlers
    ):
        pywebview_logger.addHandler(handler)
    pywebview_logger.setLevel(logging.INFO)
    return logger


def write_launcher_log(
    root: Path,
    message: str,
    *,
    level: int = logging.INFO,
    selected_data_dir: Path | None = None,
) -> None:
    configure_launcher_logging(root, selected_data_dir).log(level, message.rstrip())


def close_launcher_logging(root: Path, selected_data_dir: Path | None = None) -> None:
    target = launcher_log_path(root, selected_data_dir).resolve()
    handlers: set[logging.Handler] = set()
    for logger in (
        logging.getLogger(f"i-love-learning.launcher.{target}"),
        logging.getLogger("pywebview"),
    ):
        for handler in tuple(logger.handlers):
            if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == target:
                logger.removeHandler(handler)
                handlers.add(handler)
    for handler in handlers:
        handler.close()


def summarize_data_directory(path: Path) -> dict[str, object]:
    path = path.resolve()
    database = path / "h3cse.db"
    summary: dict[str, object] = {
        "path": str(path),
        "databaseExists": database.is_file(),
        "questions": 0,
        "attempts": 0,
        "projects": 0,
        "integrity": "not-created",
        "hasUserData": False,
    }
    if not database.is_file():
        return summary
    try:
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        try:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "questions" not in tables:
                summary["integrity"] = "unknown-schema"
                summary["hasUserData"] = True
                return summary
            summary["questions"] = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            summary["attempts"] = connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
            summary["projects"] = connection.execute("SELECT COUNT(*) FROM learning_projects").fetchone()[0]
            subjects = connection.execute("SELECT COUNT(*) FROM subjects").fetchone()[0]
            summary["integrity"] = connection.execute("PRAGMA quick_check").fetchone()[0]
            summary["hasUserData"] = bool(summary["questions"] or summary["attempts"] or subjects)
        finally:
            connection.close()
    except sqlite3.Error as exc:
        summary["integrity"] = f"error: {exc}"
        summary["hasUserData"] = True
    return summary


def ensure_writable_data_directory(path: Path) -> Path:
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise ValueError("所选数据位置不是文件夹")
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".write-test-{uuid.uuid4().hex}"
    try:
        probe.write_text("ok", encoding="ascii")
    finally:
        probe.unlink(missing_ok=True)
    return path


def migrate_data_directory(source: Path, target: Path) -> dict[str, object]:
    source = source.resolve()
    target = target.resolve()
    if source == target:
        return summarize_data_directory(target)
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("原数据目录和目标目录不能互相嵌套")
    if not (source / "h3cse.db").is_file():
        raise ValueError("原数据目录中没有数据库，无法迁移")
    if target.exists() and any(target.iterdir()):
        raise ValueError("目标目录不是空目录；为防止覆盖数据，迁移已停止")

    stage = target.parent / f".{target.name}.migration-{uuid.uuid4().hex}"
    if stage.exists():
        raise ValueError("迁移暂存目录已存在")
    stage.mkdir(parents=True)
    try:
        source_db = sqlite3.connect(f"file:{(source / 'h3cse.db').as_posix()}?mode=ro", uri=True)
        target_db = sqlite3.connect(stage / "h3cse.db")
        try:
            source_db.backup(target_db)
        finally:
            target_db.close()
            source_db.close()
        for child in source.iterdir():
            if child.name in {
                "h3cse.db",
                "h3cse.db-wal",
                "h3cse.db-shm",
                ".portable-launcher.json",
                ".portable-launcher.lock",
                "logs",
            }:
                continue
            destination = stage / child.name
            if child.is_dir():
                shutil.copytree(child, destination)
            else:
                shutil.copy2(child, destination)
        migrated = summarize_data_directory(stage)
        if migrated["integrity"] != "ok":
            raise ValueError(f"迁移后的数据库检查失败：{migrated['integrity']}")
        if target.exists():
            target.rmdir()
        stage.replace(target)
        return summarize_data_directory(target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


class SingleInstance:
    def __init__(self, root: Path) -> None:
        self.path = root / ".portable-launcher.lock"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        import msvcrt

        self.handle = self.path.open("a+b")
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        return True

    def release(self) -> None:
        if not self.handle:
            return
        if os.name == "nt":
            import msvcrt

            self.handle.seek(0)
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        self.handle.close()
        self.handle = None


class LauncherApi:
    def __init__(self, launcher: Launcher) -> None:
        self._launcher = launcher

    def _failure(self, action: str, message: str, exc: Exception) -> dict[str, object]:
        self._launcher.logger.exception("%s failed", action)
        return {"ok": False, "message": f"{message}：{exc}"}

    def get_state(self) -> dict[str, object]:
        try:
            return self._launcher.state()
        except Exception as exc:
            return self._failure("get_state", "读取启动器状态失败", exc)

    def start_service(self, payload: dict[str, object]) -> dict[str, object]:
        try:
            return self._launcher.start_service(payload)
        except Exception as exc:
            self._launcher.status = "启动失败"
            self._launcher.message = f"启动失败：{exc}"
            return self._failure("start_service", "启动失败", exc)

    def stop_service(self) -> dict[str, object]:
        try:
            return self._launcher.stop_service()
        except Exception as exc:
            return self._failure("stop_service", "停止失败", exc)

    def open_browser(self) -> dict[str, object]:
        try:
            self._launcher.open_browser()
            return {"ok": True}
        except Exception as exc:
            return self._failure("open_browser", "打开浏览器失败", exc)

    def open_data_dir(self) -> dict[str, object]:
        try:
            self._launcher.open_data_dir()
            return {"ok": True}
        except Exception as exc:
            return self._failure("open_data_dir", "打开数据目录失败", exc)

    def open_logs_dir(self) -> dict[str, object]:
        try:
            self._launcher.open_logs_dir()
            return {"ok": True}
        except Exception as exc:
            return self._failure("open_logs_dir", "打开日志目录失败", exc)

    def select_data_dir(self) -> dict[str, object]:
        try:
            return self._launcher.select_data_dir()
        except Exception as exc:
            return self._failure("select_data_dir", "选择数据目录失败", exc)

    def back_to_settings(self) -> dict[str, object]:
        try:
            self._launcher.show_launch_page()
            return {"ok": True}
        except Exception as exc:
            return self._failure("back_to_settings", "返回设置失败", exc)


class Launcher:
    def __init__(self) -> None:
        self.root_path = portable_root()
        self.port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
        config = load_launcher_config(self.root_path)
        self.data_path = normalize_data_path(config["data_path"], self.root_path)
        self.version_info = load_version_info(self.root_path)
        self.logger = configure_launcher_logging(self.root_path, self.data_path)
        self.process: subprocess.Popen[str] | None = None
        self.window = None
        self.status = "未启动"
        self.message = ""
        self.current_open_mode: str | None = None
        self.local_only = bool(config["local_only"])
        self.open_mode = config["open_mode"]
        self.legacy_config = bool(config["legacy_config"])
        self.api = LauncherApi(self)
        self._lock = threading.RLock()

    def exposed_api_callables(self) -> tuple[object, ...]:
        return tuple(getattr(self.api, name) for name in EXPOSED_API_METHODS)

    def state(self) -> dict[str, object]:
        with self._lock:
            running = self.process is not None and self.process.poll() is None
            return {
                "appName": APP_NAME,
                "status": self.status,
                "message": self.message,
                "running": running,
                "localOnly": self.local_only,
                "openMode": self.open_mode,
                "hasSavedOpenMode": self.open_mode in VALID_OPEN_MODES,
                "localUrl": local_url(self.port),
                "lanUrl": lan_url(self.port),
                "activeUrl": local_url(self.port) if self.local_only else lan_url(self.port),
                "dataPath": str(self.data_path),
                "dataSummary": summarize_data_directory(self.data_path),
                "logPath": str(launcher_log_path(self.root_path, self.data_path)),
                "version": self.version_info["version"],
                "buildCommit": self.version_info["build_commit"],
                "buildTime": self.version_info["build_time"],
                "port": self.port,
            }

    def _host(self) -> str:
        return "127.0.0.1" if self.local_only else "0.0.0.0"

    def run(self) -> None:
        self.logger.info(
            "launcher starting version=%s commit=%s build_time=%s frozen=%s python=%s platform=%s executable=%s root=%s data=%s port=%s",
            self.version_info["version"],
            self.version_info["build_commit"],
            self.version_info["build_time"],
            bool(getattr(sys, "frozen", False)),
            platform.python_version(),
            platform.platform(),
            sys.executable,
            self.root_path,
            self.data_path,
            self.port,
        )
        try:
            import webview
        except ImportError as exc:
            self.logger.exception("pywebview import failed")
            messagebox.showerror(
                APP_NAME,
                "当前 Portable 缺少 GUI 组件 pywebview，无法打开启动界面。\n\n"
                "将切换到浏览器 Web 兜底模式。请重新下载完整 Portable 包，或在开发环境中安装 Windows Portable 依赖。",
            )
            BrowserFallbackLauncher(self.root_path, self.port, str(exc)).run()
            return

        self.window = webview.create_window(
            APP_NAME,
            html=self._launch_html(),
            width=1180,
            height=760,
            min_size=(920, 620),
        )
        self.window.expose(*self.exposed_api_callables())
        self.logger.info("webview API allowlist registered methods=%s", ",".join(EXPOSED_API_METHODS))
        try:
            self.window.events.closing += self._on_window_closing
            self.window.events.loaded += self._on_webview_loaded
        except Exception:
            self.logger.exception("webview event registration failed")

        try:
            webview.start(debug=False)
        except Exception as exc:
            self.logger.exception("webview initialization failed")
            self.stop()
            messagebox.showerror(
                APP_NAME,
                "软件内 GUI 初始化失败，可能是系统缺少 Microsoft Edge WebView2 Runtime。\n\n"
                "将切换到浏览器 Web 兜底模式。请安装 WebView2 Runtime 后重试 GUI 模式。\n\n"
                f"错误信息：{exc}",
            )
            BrowserFallbackLauncher(self.root_path, self.port, str(exc)).run()
        finally:
            self.stop()

    def start_service(self, payload: dict[str, object]) -> dict[str, object]:
        open_mode = str(payload.get("openMode") or "")
        if open_mode not in VALID_OPEN_MODES:
            self.message = "首次使用请先选择打开方式：软件内 GUI 或浏览器 Web。"
            self.show_launch_page()
            return {"ok": False, "message": self.message}

        local_only = bool(payload.get("localOnly", True))
        requested_data_path = ensure_writable_data_directory(
            normalize_data_path(payload.get("dataPath") or self.data_path, self.root_path)
        )
        self.logger.info(
            "start requested open_mode=%s local_only=%s port=%s requested_data=%s",
            open_mode,
            local_only,
            self.port,
            requested_data_path,
        )
        if self.process and self.process.poll() is None and requested_data_path != self.data_path:
            return {"ok": False, "message": "请先停止当前服务，再切换数据目录。"}

        if requested_data_path != self.data_path:
            source_summary = summarize_data_directory(self.data_path)
            target_summary = summarize_data_directory(requested_data_path)
            target_is_empty = not any(requested_data_path.iterdir())
            if source_summary["hasUserData"] and target_is_empty:
                if not bool(payload.get("confirmMigration")):
                    return {
                        "ok": False,
                        "requiresMigration": True,
                        "source": source_summary,
                        "target": target_summary,
                        "message": "目标目录为空，是否把当前完整数据迁移到新目录？",
                    }
                self.logger.info("data migration requested source=%s target=%s", self.data_path, requested_data_path)
                migrate_data_directory(self.data_path, requested_data_path)
            self._switch_data_path(requested_data_path)
        with self._lock:
            self.local_only = local_only
            self.open_mode = open_mode
            self.current_open_mode = open_mode

        if self.process and self.process.poll() is None:
            self.logger.info("service already running pid=%s", self.process.pid)
            if open_mode == "gui":
                self._load_url(local_url(self.port))
            else:
                self.open_browser()
                self.show_control_page("服务已经在运行。")
            return {"ok": True, "message": "服务已经在运行。"}

        host = self._host()
        if not is_port_available(host, self.port):
            self.logger.error("port unavailable host=%s port=%s", host, self.port)
            self.status = "端口被占用"
            self.message = (
                f"端口 {self.port} 已被占用。请先关闭正在运行的我爱学习网页端、"
                "旧源码服务或另一个 Portable 启动器。"
            )
            self.show_launch_page()
            return {"ok": False, "message": self.message}

        save_launcher_config(
            self.root_path,
            local_only=self.local_only,
            open_mode=open_mode,
            selected_data_dir=self.data_path,
        )
        self.legacy_config = False
        self._start_process(host)
        self.status = "正在启动"
        self.message = "正在启动本地服务，请稍候……"
        self.show_control_page(self.message)
        threading.Thread(target=self._watch_process, daemon=True).start()
        threading.Thread(target=self._wait_until_ready, args=(open_mode,), daemon=True).start()
        return {"ok": True, "message": self.message}

    def _start_process(self, host: str) -> None:
        env = os.environ.copy()
        env["STUDY_DATA_DIR"] = str(self.data_path)
        env.setdefault("STUDY_BACKUP_DIR", str(Path.home() / "Documents" / "I-Love-Learning-Backup"))
        env["STUDY_APP_VERSION"] = self.version_info["version"]
        env["STUDY_BUILD_COMMIT"] = self.version_info["build_commit"]
        env["PORT"] = str(self.port)
        command = [sys.executable, "--serve", "--host", host, "--port", str(self.port)]
        if not getattr(sys, "frozen", False):
            command = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--serve",
                "--host",
                host,
                "--port",
                str(self.port),
            ]
        self.process = subprocess.Popen(
            command,
            cwd=str(self.root_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.logger.info("service process started pid=%s host=%s port=%s", self.process.pid, host, self.port)

    def _wait_until_ready(self, open_mode: str) -> None:
        for _ in range(60):
            if not (self.process and self.process.poll() is None):
                self.logger.error("service exited before health check passed")
                return
            if self._health_ok():
                self.logger.info("service health check passed url=%s/health", local_url(self.port))
                self.status = "运行中"
                self.message = "服务已启动。"
                if open_mode == "gui":
                    self._load_url(local_url(self.port))
                else:
                    self.open_browser()
                    self.show_control_page("服务已启动，已在浏览器中打开。")
                return
            time.sleep(0.35)
        self.status = "启动超时"
        self.message = (
            "服务启动超时，请停止后重试；如反复失败，请查看 "
            f"{launcher_log_path(self.root_path, self.data_path)} 和 {self.data_path / 'logs' / 'app.log'}。"
        )
        self.logger.error("service health check timed out url=%s/health", local_url(self.port))
        self.show_control_page(self.message)

    def _health_ok(self) -> bool:
        try:
            with urllib.request.urlopen(f"{local_url(self.port)}/health", timeout=1.2) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def _watch_process(self) -> None:
        output: list[str] = []
        process = self.process
        if process is None:
            return
        while True:
            line = process.stdout.readline() if process.stdout else ""
            if line:
                clean_line = line.rstrip()
                output.append(clean_line)
                output = output[-12:]
                self.logger.info("service | %s", clean_line)
            if process.poll() is not None:
                break
            time.sleep(0.1)
        if process.returncode not in (0, None):
            self.logger.error("service process exited returncode=%s", process.returncode)
            self.status = f"异常退出（{process.returncode}）"
            details = "\n".join(output[-8:]) or "没有捕获到详细日志。"
            self.message = "服务异常退出：\n" + details
            self.show_control_page(self.message)
        elif self.process is process:
            self.logger.info("service process stopped returncode=%s", process.returncode)
            self.status = "已停止"
            self.message = "服务已停止。"
            self.show_launch_page()

    def stop_service(self) -> dict[str, object]:
        self.logger.info("stop requested")
        self.stop()
        self.status = "已停止"
        self.message = "服务已停止，可以重新选择打开方式。"
        self.show_launch_page()
        return {"ok": True, "message": self.message}

    def stop(self) -> None:
        process = self.process
        if process and process.poll() is None:
            self.logger.info("terminating service pid=%s", process.pid)
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.logger.warning("service did not stop in time; killing pid=%s", process.pid)
                process.kill()
        self.process = None

    def open_browser(self) -> None:
        webbrowser.open(local_url(self.port) if self.local_only else lan_url(self.port))

    def _switch_data_path(self, selected: Path) -> None:
        selected = ensure_writable_data_directory(selected)
        if selected == self.data_path:
            return
        previous = self.data_path
        self.logger.info("switching data directory from=%s to=%s", previous, selected)
        close_launcher_logging(self.root_path, previous)
        self.data_path = selected
        self.logger = configure_launcher_logging(self.root_path, self.data_path)
        self.logger.info("data directory selected path=%s", self.data_path)

    def select_data_dir(self) -> dict[str, object]:
        if self.window is None:
            return {"ok": False, "message": "启动器窗口尚未就绪"}
        import webview

        selected = self.window.create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=str(self.data_path),
        )
        if not selected:
            return {"ok": False, "cancelled": True}
        path = normalize_data_path(selected[0], self.root_path)
        return {"ok": True, "path": str(path), "summary": summarize_data_directory(path)}

    def open_data_dir(self) -> None:
        path = self.data_path
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            webbrowser.open(path.as_uri())

    def open_logs_dir(self) -> None:
        path = launcher_log_path(self.root_path, self.data_path).parent
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            webbrowser.open(path.as_uri())

    def show_launch_page(self) -> None:
        self._load_html(self._launch_html())

    def show_control_page(self, message: str = "") -> None:
        if message:
            self.message = message
        self._load_html(self._control_html())

    def _load_url(self, url: str) -> None:
        if self.window is not None:
            self.window.load_url(url)

    def _load_html(self, content: str) -> None:
        if self.window is not None:
            self.window.load_html(content)

    def _on_window_closing(self) -> None:
        self.logger.info("launcher window closing")
        self.stop()

    def _on_webview_loaded(self) -> None:
        if self.window is None:
            return
        try:
            methods = self.window.evaluate_js(
                "Object.keys((window.pywebview && window.pywebview.api) || {}).sort()"
            )
            self.logger.info("webview bridge loaded methods=%s", methods)
            missing = [name for name in EXPOSED_API_METHODS if name not in (methods or [])]
            if missing:
                self.logger.error("webview bridge missing methods=%s", ",".join(missing))
        except Exception:
            self.logger.exception("webview bridge inspection failed")

    def _launch_html(self) -> str:
        state_json = json.dumps(self.state(), ensure_ascii=False)
        return _shell_html(
            title="启动设置",
            body=f"""
            <main class="card">
              <div class="hero">
                <div>
                  <p class="eyebrow">Windows Portable</p>
                  <h1>{APP_NAME}</h1>
                  <p class="muted">请选择访问范围和打开方式。首次使用必须选择打开方式；之后会记住你的选择，但每次启动前都可以切换。</p>
                </div>
                <span class="badge" id="statusBadge">未启动</span>
              </div>

              <section class="section">
                <h2>访问范围</h2>
                <label class="option">
                  <input type="radio" name="localOnly" value="true">
                  <span><strong>仅本机访问</strong><small>只允许当前电脑访问，默认更安全。</small></span>
                </label>
                <label class="option">
                  <input type="radio" name="localOnly" value="false">
                  <span><strong>允许同一局域网访问</strong><small>手机可通过家庭/办公室内网地址访问同一份数据。</small></span>
                </label>
              </section>

              <section class="section">
                <h2>打开方式</h2>
                <label class="option">
                  <input type="radio" name="openMode" value="gui">
                  <span><strong>软件内打开（GUI）</strong><small>在当前窗口中打开，体验更像桌面软件。</small></span>
                </label>
                <label class="option">
                  <input type="radio" name="openMode" value="web">
                  <span><strong>浏览器打开（Web）</strong><small>用系统浏览器打开，兼容性最好，也方便复制地址。</small></span>
                </label>
                <p class="notice" id="firstRunNotice">首次使用请先选择打开方式。</p>
              </section>

              <section class="section">
                <h2>数据目录</h2>
                <div class="data-picker">
                  <input id="dataPathInput" autocomplete="off" spellcheck="false" aria-label="数据目录">
                  <button id="selectDataButton" type="button">选择目录</button>
                </div>
                <p class="notice" id="dataSummary"></p>
              </section>

              <dl class="info-grid">
                <div><dt>电脑地址</dt><dd id="localUrl"></dd></div>
                <div><dt>手机地址</dt><dd id="lanUrl"></dd></div>
                <div><dt>启动日志</dt><dd id="logPath"></dd></div>
                <div><dt>软件版本</dt><dd id="versionInfo"></dd></div>
              </dl>

              <p class="error" id="message"></p>
              <div class="actions">
                <button class="primary" id="startButton">启动</button>
                <button id="openDataButton">打开数据目录</button>
                <button id="openLogsButton">打开日志目录</button>
                <button id="openBrowserButton">在浏览器中打开</button>
                <button id="retryBridgeButton" hidden>重新载入接口</button>
              </div>
            </main>
            <script>
              const initialState = {state_json};
              function setChecked(name, value) {{
                const target = document.querySelector(`input[name="${{name}}"][value="${{value}}"]`);
                if (target) target.checked = true;
              }}
              function selected(name) {{
                const target = document.querySelector(`input[name="${{name}}"]:checked`);
                return target ? target.value : null;
              }}
              let apiReady = false;
              function detectApi() {{
                if (apiMethod('start_service', 'startService')) {{
                  apiReady = true;
                  refreshDerived();
                  return true;
                }}
                apiReady = false;
                return false;
              }}
              function waitForApi(timeoutMs = 6000) {{
                return new Promise(resolve => {{
                  if (detectApi()) {{
                    resolve(true);
                    return;
                  }}
                  const startedAt = Date.now();
                  const timer = window.setInterval(() => {{
                    if (detectApi()) {{
                      window.clearInterval(timer);
                      resolve(true);
                    }} else if (Date.now() - startedAt >= timeoutMs) {{
                      window.clearInterval(timer);
                      resolve(false);
                    }}
                  }}, 100);
                }});
              }}
              function apiMethod(snakeName, camelName) {{
                const api = window.pywebview && window.pywebview.api;
                if (!api) return null;
                const method = api[snakeName] || api[camelName];
                return typeof method === 'function' ? method.bind(api) : null;
              }}
              function describeError(error) {{
                if (!error) return '未知错误';
                if (typeof error === 'string') return error;
                if (error.message) return error.message;
                try {{
                  return JSON.stringify(error);
                }} catch (_) {{
                  return String(error);
                }}
              }}
              function refreshDerived() {{
                const localOnly = selected('localOnly') !== 'false';
                document.querySelector('#openBrowserButton').disabled = true;
                document.querySelector('#startButton').disabled = !selected('openMode');
              }}
              function render(state) {{
                setChecked('localOnly', String(state.localOnly));
                if (state.openMode) setChecked('openMode', state.openMode);
                document.querySelector('#statusBadge').textContent = state.status;
                document.querySelector('#localUrl').textContent = state.localUrl;
                document.querySelector('#lanUrl').textContent = state.lanUrl;
                document.querySelector('#dataPathInput').value = state.dataPath;
                document.querySelector('#logPath').textContent = state.logPath;
                document.querySelector('#versionInfo').textContent =
                  `v${{state.version}} · ${{state.buildCommit || 'development'}}`;
                renderDataSummary(state.dataSummary);
                document.querySelector('#message').textContent = state.message || '';
                document.querySelector('#firstRunNotice').style.display = state.hasSavedOpenMode ? 'none' : 'block';
                refreshDerived();
              }}
              function renderDataSummary(summary) {{
                const target = document.querySelector('#dataSummary');
                if (!summary || !summary.databaseExists) {{
                  target.textContent = '该目录尚未创建数据库，首次启动时会建立空数据。';
                  return;
                }}
                target.textContent =
                  `检测到已有数据：${{summary.projects}} 个项目，${{summary.questions}} 道题，数据库检查 ${{summary.integrity}}。`;
              }}
              async function start(confirmMigration = false) {{
                const mode = selected('openMode');
                if (!mode) {{
                  document.querySelector('#message').textContent = '首次使用请先选择打开方式。';
                  return;
                }}
                document.querySelector('#startButton').disabled = true;
                document.querySelector('#message').textContent = '正在连接启动器……';
                const ready = await waitForApi();
                if (!ready) {{
                  document.querySelector('#message').textContent =
                    '启动器接口没有正确载入。详细信息已写入：' + initialState.logPath;
                  document.querySelector('#retryBridgeButton').hidden = false;
                  document.querySelector('#startButton').disabled = false;
                  return;
                }}
                const startService = apiMethod('start_service', 'startService');
                if (!startService) {{
                  document.querySelector('#message').textContent =
                    '启动器接口已载入，但没有找到启动方法。请点击“重新载入接口”；日志：' + initialState.logPath;
                  document.querySelector('#retryBridgeButton').hidden = false;
                  document.querySelector('#startButton').disabled = false;
                  return;
                }}
                document.querySelector('#message').textContent = '正在启动……';
                try {{
                  const result = await startService({{
                    localOnly: selected('localOnly') !== 'false',
                    openMode: mode,
                    dataPath: document.querySelector('#dataPathInput').value,
                    confirmMigration
                  }});
                  if (!result.ok) {{
                    if (result.requiresMigration) {{
                      const accepted = window.confirm(
                        `${{result.message}}\n\n原目录：${{result.source.path}}\n题目：${{result.source.questions}}\n\n新目录：${{result.target.path}}`
                      );
                      if (accepted) return start(true);
                    }}
                    document.querySelector('#message').textContent = result.message || '启动失败。';
                    document.querySelector('#startButton').disabled = false;
                  }}
                }} catch (error) {{
                  document.querySelector('#message').textContent = '启动器接口调用失败：' + describeError(error);
                  document.querySelector('#startButton').disabled = false;
                }}
              }}
              window.addEventListener('pywebviewready', () => {{
                apiReady = true;
                refreshDerived();
              }});
              window.setTimeout(detectApi, 0);
              window.setTimeout(detectApi, 300);
              window.setTimeout(detectApi, 1000);
              render(initialState);
              document.querySelectorAll('input').forEach(input => input.addEventListener('change', refreshDerived));
              document.querySelector('#startButton').addEventListener('click', start);
              document.querySelector('#selectDataButton').addEventListener('click', async () => {{
                const selectDataDir = apiMethod('select_data_dir', 'selectDataDir');
                if (!selectDataDir) return;
                const result = await selectDataDir();
                if (result.ok) {{
                  document.querySelector('#dataPathInput').value = result.path;
                  renderDataSummary(result.summary);
                }}
              }});
              document.querySelector('#openDataButton').addEventListener('click', () => {{
                const openDataDir = apiMethod('open_data_dir', 'openDataDir');
                if (openDataDir) openDataDir();
              }});
              document.querySelector('#openLogsButton').addEventListener('click', () => {{
                const openLogsDir = apiMethod('open_logs_dir', 'openLogsDir');
                if (openLogsDir) openLogsDir();
              }});
              document.querySelector('#openBrowserButton').addEventListener('click', () => {{
                const openBrowser = apiMethod('open_browser', 'openBrowser');
                if (openBrowser) openBrowser();
              }});
              document.querySelector('#retryBridgeButton').addEventListener('click', () => window.location.reload());
            </script>
            """,
        )

    def _control_html(self) -> str:
        state = self.state()
        message = html.escape(str(state["message"] or "")).replace("\n", "<br>")
        return _shell_html(
            title="运行控制",
            body=f"""
            <main class="card">
              <div class="hero">
                <div>
                  <p class="eyebrow">Web 模式</p>
                  <h1>{APP_NAME} 正在运行</h1>
                  <p class="muted">浏览器窗口已打开。你可以保留此窗口用于停止服务、打开数据目录或重新打开浏览器。</p>
                </div>
                <span class="badge">{html.escape(str(state["status"]))}</span>
              </div>
              <dl class="info-grid">
                <div><dt>电脑地址</dt><dd>{html.escape(str(state["localUrl"]))}</dd></div>
                <div><dt>手机地址</dt><dd>{html.escape(str(state["lanUrl"]))}</dd></div>
                <div><dt>数据目录</dt><dd>{html.escape(str(state["dataPath"]))}</dd></div>
                <div><dt>启动日志</dt><dd>{html.escape(str(state["logPath"]))}</dd></div>
                <div><dt>软件版本</dt><dd>v{html.escape(str(state["version"]))} · {html.escape(str(state["buildCommit"]))}</dd></div>
              </dl>
              <p class="notice">{message}</p>
              <div class="actions">
                <button class="primary" id="openBrowserButton">重新打开浏览器</button>
                <button id="openDataButton">打开数据目录</button>
                <button id="openLogsButton">打开日志目录</button>
                <button id="stopButton">停止并返回设置</button>
              </div>
            </main>
            <script>
              function apiMethod(snakeName, camelName) {{
                const api = window.pywebview && window.pywebview.api;
                if (!api) return null;
                const method = api[snakeName] || api[camelName];
                return typeof method === 'function' ? method.bind(api) : null;
              }}
              function callApi(snakeName, camelName) {{
                const method = apiMethod(snakeName, camelName);
                if (method) method();
              }}
              document.querySelector('#openBrowserButton').addEventListener('click', () => callApi('open_browser', 'openBrowser'));
              document.querySelector('#openDataButton').addEventListener('click', () => callApi('open_data_dir', 'openDataDir'));
              document.querySelector('#openLogsButton').addEventListener('click', () => callApi('open_logs_dir', 'openLogsDir'));
              document.querySelector('#stopButton').addEventListener('click', () => callApi('stop_service', 'stopService'));
            </script>
            """,
        )


def _shell_html(*, title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(APP_NAME)} - {html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #eef4ff;
      --card: rgba(255, 255, 255, .92);
      --text: #132238;
      --muted: #667085;
      --line: #dce5f5;
      --primary: #2563eb;
      --primary-dark: #1d4ed8;
      --danger: #dc2626;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 15% 10%, rgba(59,130,246,.20), transparent 28%),
        linear-gradient(135deg, #f8fbff, var(--bg));
      display: grid;
      place-items: center;
      padding: 28px;
    }}
    .card {{
      width: min(920px, 100%);
      background: var(--card);
      border: 1px solid rgba(148, 163, 184, .28);
      border-radius: 28px;
      box-shadow: 0 24px 70px rgba(37, 99, 235, .13);
      padding: 30px;
      backdrop-filter: blur(18px);
    }}
    .hero {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
      margin-bottom: 22px;
    }}
    .eyebrow {{
      margin: 0 0 8px;
      color: var(--primary);
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
      font-size: 12px;
    }}
    h1 {{ margin: 0; font-size: 34px; }}
    h2 {{ margin: 0 0 12px; font-size: 17px; }}
    .muted {{ margin: 10px 0 0; color: var(--muted); line-height: 1.7; }}
    .badge {{
      white-space: nowrap;
      border-radius: 999px;
      padding: 8px 13px;
      color: #1e3a8a;
      background: #dbeafe;
      font-size: 13px;
      font-weight: 700;
    }}
    .section {{
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 18px;
      margin: 14px 0;
      background: rgba(255,255,255,.66);
    }}
    .option {{
      display: flex;
      gap: 12px;
      align-items: flex-start;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 16px;
      margin-top: 10px;
      cursor: pointer;
      background: white;
      transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease;
    }}
    .option:hover {{
      border-color: #93c5fd;
      box-shadow: 0 10px 28px rgba(37, 99, 235, .10);
      transform: translateY(-1px);
    }}
    .option input {{ margin-top: 4px; accent-color: var(--primary); }}
    .option strong {{ display: block; margin-bottom: 4px; }}
    .option small {{ color: var(--muted); line-height: 1.55; }}
    .data-picker {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
    }}
    .data-picker input {{
      width: 100%;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 13px;
      padding: 11px 13px;
      color: var(--text);
      background: white;
      font: inherit;
    }}
    .data-picker input:focus {{
      outline: 2px solid rgba(37,99,235,.2);
      border-color: #60a5fa;
    }}
    .info-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin: 18px 0;
    }}
    .info-grid div {{
      border: 1px solid var(--line);
      background: rgba(255,255,255,.74);
      border-radius: 16px;
      padding: 12px 14px;
      min-width: 0;
    }}
    .info-grid div:last-child {{ grid-column: 1 / -1; }}
    dt {{ color: var(--muted); font-size: 12px; margin-bottom: 5px; }}
    dd {{ margin: 0; word-break: break-all; font-weight: 650; }}
    .notice {{
      margin: 12px 0;
      padding: 12px 14px;
      border-radius: 14px;
      background: #eff6ff;
      color: #1e40af;
      line-height: 1.65;
    }}
    .error {{
      margin: 12px 0;
      color: var(--danger);
      min-height: 24px;
      white-space: pre-wrap;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 18px;
    }}
    button {{
      border: 0;
      border-radius: 13px;
      padding: 11px 16px;
      font: inherit;
      font-weight: 700;
      color: #1f2937;
      background: #e5eaf5;
      cursor: pointer;
    }}
    button:hover:not(:disabled) {{ filter: brightness(.98); }}
    button:disabled {{ cursor: not-allowed; opacity: .55; }}
    button.primary {{ background: var(--primary); color: white; }}
    button.primary:hover:not(:disabled) {{ background: var(--primary-dark); }}
    @media (max-width: 720px) {{
      body {{ padding: 14px; place-items: start center; }}
      .card {{ padding: 20px; border-radius: 22px; }}
      .hero {{ display: block; }}
      .badge {{ display: inline-block; margin-top: 12px; }}
      .info-grid {{ grid-template-columns: 1fr; }}
      .data-picker {{ grid-template-columns: 1fr; }}
      .actions button {{ width: 100%; }}
    }}
  </style>
</head>
<body>
{body}
</body>
</html>"""


class BrowserFallbackLauncher:
    """Minimal Tk fallback for systems that cannot initialize the WebView shell."""

    def __init__(self, root_path: Path, port: int, reason: str = "") -> None:
        self.root_path = root_path
        self.port = port
        self.reason = reason
        saved = load_launcher_config(root_path)
        self.selected_data_path = normalize_data_path(saved["data_path"], root_path)
        self.version_info = load_version_info(root_path)
        self.logger = configure_launcher_logging(root_path, self.selected_data_path)
        self.process: subprocess.Popen[str] | None = None
        self.logger.warning("browser fallback launcher opened reason=%s", reason or "unknown")

        self.window = Tk()
        self.window.title(f"{APP_NAME} - Web 兜底模式")
        self.window.geometry("640x420")
        self.window.minsize(560, 360)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.local_only = BooleanVar(self.window, value=bool(saved["local_only"]))
        self.status = StringVar(self.window, value="未启动")
        self.address = StringVar(self.window)
        self.data_path = StringVar(self.window, value=str(self.selected_data_path))
        self._build_ui()
        self._refresh_address()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill=BOTH, expand=True)
        ttk.Label(frame, text=APP_NAME, font=("", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text=f"v{self.version_info['version']} · {self.version_info['build_commit']}", foreground="#666666").pack(anchor="w")
        ttk.Label(
            frame,
            text="软件内 GUI 当前不可用，已切换为浏览器 Web 兜底模式。你仍可正常启动服务并使用浏览器访问。",
            foreground="#b45309",
            wraplength=580,
        ).pack(fill="x", pady=(10, 8))
        if self.reason:
            ttk.Label(frame, text=f"原因：{self.reason}", foreground="#666666", wraplength=580).pack(fill="x")

        mode = ttk.LabelFrame(frame, text="访问范围", padding=12)
        mode.pack(fill="x", pady=(14, 10))
        ttk.Radiobutton(mode, text="仅本机访问", variable=self.local_only, value=True, command=self._refresh_address).pack(anchor="w")
        ttk.Radiobutton(mode, text="允许同一局域网访问", variable=self.local_only, value=False, command=self._refresh_address).pack(anchor="w", pady=(6, 0))

        info = ttk.Frame(frame)
        info.pack(fill="x", pady=(4, 12))
        ttk.Label(info, text="状态").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Label(info, textvariable=self.status).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(info, text="地址").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Label(info, textvariable=self.address).grid(row=1, column=1, sticky="w", pady=3)
        ttk.Label(info, text="数据目录").grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=3)
        ttk.Label(info, textvariable=self.data_path, wraplength=500).grid(row=2, column=1, sticky="w", pady=3)
        ttk.Button(info, text="选择", command=self.select_data_dir).grid(row=2, column=2, padx=(8, 0), pady=3)
        info.columnconfigure(1, weight=1)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(4, 0))
        self.start_button = ttk.Button(buttons, text="浏览器打开（Web）", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止", command=self.stop, state=DISABLED)
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="打开数据目录", command=self.open_data_dir).pack(side="left")
        ttk.Button(buttons, text="打开日志目录", command=self.open_logs_dir).pack(side="left", padx=8)
        ttk.Button(buttons, text="退出", command=self.close).pack(side="right")

    def _refresh_address(self) -> None:
        self.address.set(local_url(self.port) if self.local_only.get() else lan_url(self.port))

    def _host(self) -> str:
        return "127.0.0.1" if self.local_only.get() else "0.0.0.0"

    def start(self) -> None:
        if self.process and self.process.poll() is None:
            self.open_browser()
            return
        host = self._host()
        if not is_port_available(host, self.port):
            self.logger.error("fallback port unavailable host=%s port=%s", host, self.port)
            messagebox.showerror(APP_NAME, f"端口 {self.port} 已被占用，请先关闭旧服务后重试。")
            self.status.set("端口被占用")
            return
        requested_data = ensure_writable_data_directory(
            normalize_data_path(self.data_path.get(), self.root_path)
        )
        if requested_data != self.selected_data_path:
            source_summary = summarize_data_directory(self.selected_data_path)
            if source_summary["hasUserData"] and not any(requested_data.iterdir()):
                accepted = messagebox.askyesno(
                    APP_NAME,
                    f"目标目录为空，是否迁移当前完整数据？\n\n原目录：{self.selected_data_path}\n新目录：{requested_data}",
                )
                if not accepted:
                    return
                migrate_data_directory(self.selected_data_path, requested_data)
            close_launcher_logging(self.root_path, self.selected_data_path)
            self.selected_data_path = requested_data
            self.logger = configure_launcher_logging(self.root_path, self.selected_data_path)
            self.data_path.set(str(self.selected_data_path))
        save_launcher_config(
            self.root_path,
            local_only=bool(self.local_only.get()),
            open_mode="web",
            selected_data_dir=self.selected_data_path,
        )
        env = os.environ.copy()
        env["STUDY_DATA_DIR"] = str(self.selected_data_path)
        env.setdefault("STUDY_BACKUP_DIR", str(Path.home() / "Documents" / "I-Love-Learning-Backup"))
        env["STUDY_APP_VERSION"] = self.version_info["version"]
        env["STUDY_BUILD_COMMIT"] = self.version_info["build_commit"]
        env["PORT"] = str(self.port)
        command = [sys.executable, "--serve", "--host", host, "--port", str(self.port)]
        if not getattr(sys, "frozen", False):
            command = [sys.executable, str(Path(__file__).resolve()), "--serve", "--host", host, "--port", str(self.port)]
        self.process = subprocess.Popen(
            command,
            cwd=str(self.root_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.logger.info("fallback service process started pid=%s host=%s port=%s", self.process.pid, host, self.port)
        self.status.set("正在启动")
        self.start_button.config(state=DISABLED)
        self.stop_button.config(state=NORMAL)
        threading.Thread(target=self._watch_process, daemon=True).start()
        self.window.after(900, self.open_browser)

    def _watch_process(self) -> None:
        process = self.process
        if process is None:
            return
        while True:
            line = process.stdout.readline() if process.stdout else ""
            if line:
                self.logger.info("service | %s", line.rstrip())
            if process.poll() is not None:
                break
            self.window.after(0, lambda: self.status.set("运行中"))
            if not line:
                time.sleep(0.1)
        self.logger.info("fallback service process stopped returncode=%s", process.returncode)
        if self.process is process:
            self.window.after(0, lambda: self.status.set("已停止"))
            self.window.after(0, lambda: self.start_button.config(state=NORMAL))
            self.window.after(0, lambda: self.stop_button.config(state=DISABLED))

    def stop(self) -> None:
        process = self.process
        if process and process.poll() is None:
            self.logger.info("fallback terminating service pid=%s", process.pid)
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.logger.warning("fallback service did not stop in time; killing pid=%s", process.pid)
                process.kill()
        self.process = None
        self.status.set("已停止")
        self.start_button.config(state=NORMAL)
        self.stop_button.config(state=DISABLED)

    def open_browser(self) -> None:
        self._refresh_address()
        webbrowser.open(self.address.get())

    def open_data_dir(self) -> None:
        path = self.selected_data_path
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            webbrowser.open(path.as_uri())

    def open_logs_dir(self) -> None:
        path = launcher_log_path(self.root_path, self.selected_data_path).parent
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            webbrowser.open(path.as_uri())

    def select_data_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=str(self.selected_data_path), parent=self.window)
        if selected:
            self.data_path.set(str(normalize_data_path(selected, self.root_path)))

    def close(self) -> None:
        self.stop()
        self.window.destroy()

    def run(self) -> None:
        self.window.mainloop()


def serve(host: str, port: int) -> int:
    os.environ.setdefault("STUDY_DATA_DIR", str(data_dir()))
    os.environ.setdefault("STUDY_BACKUP_DIR", str(Path.home() / "Documents" / "I-Love-Learning-Backup"))
    os.environ["PORT"] = str(port)
    from waitress import serve as waitress_serve
    from app import app

    waitress_serve(app, host=host, port=port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Portable launcher")
    parser.add_argument("--serve", action="store_true", help="run the Flask app under Waitress")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.serve:
        return serve(args.host, args.port)

    root = portable_root()
    selected_data_dir = normalize_data_path(load_launcher_config(root)["data_path"], root)
    logger = configure_launcher_logging(root, selected_data_dir)
    instance = SingleInstance(root)
    if not instance.acquire():
        logger.warning("second launcher instance rejected")
        messagebox.showinfo(APP_NAME, "我爱学习已经在运行，请使用已打开的启动器窗口。")
        return 0
    try:
        Launcher().run()
    except Exception as exc:
        logger.exception("unhandled launcher failure")
        messagebox.showerror(
            APP_NAME,
            f"启动器发生未处理错误：{exc}\n\n详细日志：{launcher_log_path(root, selected_data_dir)}",
        )
        return 1
    finally:
        instance.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
