from __future__ import annotations

import argparse
import html
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import BOTH, DISABLED, NORMAL, BooleanVar, StringVar, Tk, ttk, messagebox


APP_NAME = "我爱学习"
DEFAULT_PORT = 23456
VALID_OPEN_MODES = {"gui", "web"}


def portable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir(root: Path | None = None) -> Path:
    return (root or portable_root()) / "data"


def launcher_config_path(root: Path | None = None) -> Path:
    return data_dir(root) / ".portable-launcher.json"


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
    path = launcher_config_path(root)
    if not path.exists():
        return {"local_only": True, "open_mode": None}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"local_only": True, "open_mode": None}
    local_only = raw.get("local_only")
    open_mode = raw.get("open_mode")
    return {
        "local_only": local_only if isinstance(local_only, bool) else True,
        "open_mode": open_mode if open_mode in VALID_OPEN_MODES else None,
    }


def save_launcher_config(root: Path, *, local_only: bool, open_mode: str) -> None:
    if open_mode not in VALID_OPEN_MODES:
        raise ValueError(f"unsupported open mode: {open_mode}")
    path = launcher_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"local_only": local_only, "open_mode": open_mode}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class SingleInstance:
    def __init__(self, root: Path) -> None:
        self.path = root / "data" / ".portable-launcher.lock"
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
        self.launcher = launcher

    def get_state(self) -> dict[str, object]:
        return self.launcher.state()

    def start_service(self, payload: dict[str, object]) -> dict[str, object]:
        return self.launcher.start_service(payload)

    def stop_service(self) -> dict[str, object]:
        return self.launcher.stop_service()

    def open_browser(self) -> dict[str, object]:
        self.launcher.open_browser()
        return {"ok": True}

    def open_data_dir(self) -> dict[str, object]:
        self.launcher.open_data_dir()
        return {"ok": True}

    def back_to_settings(self) -> dict[str, object]:
        self.launcher.show_launch_page()
        return {"ok": True}


class Launcher:
    def __init__(self) -> None:
        self.root_path = portable_root()
        self.port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
        self.process: subprocess.Popen[str] | None = None
        self.window = None
        self.status = "未启动"
        self.message = ""
        self.current_open_mode: str | None = None
        self.local_only = bool(load_launcher_config(self.root_path)["local_only"])
        self.open_mode = load_launcher_config(self.root_path)["open_mode"]
        self.api = LauncherApi(self)
        self._lock = threading.RLock()

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
                "dataPath": str(data_dir(self.root_path)),
                "port": self.port,
            }

    def _host(self) -> str:
        return "127.0.0.1" if self.local_only else "0.0.0.0"

    def run(self) -> None:
        try:
            import webview
        except ImportError as exc:
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
            js_api=self.api,
            width=1180,
            height=760,
            min_size=(920, 620),
        )
        try:
            self.window.events.closing += self._on_window_closing
        except Exception:
            pass

        try:
            webview.start(debug=False)
        except Exception as exc:
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
        with self._lock:
            self.local_only = local_only
            self.open_mode = open_mode
            self.current_open_mode = open_mode

        if self.process and self.process.poll() is None:
            if open_mode == "gui":
                self._load_url(local_url(self.port))
            else:
                self.open_browser()
                self.show_control_page("服务已经在运行。")
            return {"ok": True, "message": "服务已经在运行。"}

        host = self._host()
        if not is_port_available(host, self.port):
            self.status = "端口被占用"
            self.message = (
                f"端口 {self.port} 已被占用。请先关闭正在运行的我爱学习网页端、"
                "旧源码服务或另一个 Portable 启动器。"
            )
            self.show_launch_page()
            return {"ok": False, "message": self.message}

        save_launcher_config(self.root_path, local_only=self.local_only, open_mode=open_mode)
        self._start_process(host)
        self.status = "正在启动"
        self.message = "正在启动本地服务，请稍候……"
        self.show_control_page(self.message)
        threading.Thread(target=self._watch_process, daemon=True).start()
        threading.Thread(target=self._wait_until_ready, args=(open_mode,), daemon=True).start()
        return {"ok": True, "message": self.message}

    def _start_process(self, host: str) -> None:
        env = os.environ.copy()
        env["STUDY_DATA_DIR"] = str(data_dir(self.root_path))
        env.setdefault("STUDY_BACKUP_DIR", str(Path.home() / "Documents" / "I-Love-Learning-Backup"))
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

    def _wait_until_ready(self, open_mode: str) -> None:
        for _ in range(60):
            if not (self.process and self.process.poll() is None):
                return
            if self._health_ok():
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
        self.message = "服务启动超时，请停止后重试；如反复失败，请查看 data/logs/app.log。"
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
                output.append(line.rstrip())
                output = output[-12:]
            if process.poll() is not None:
                break
            time.sleep(0.1)
        if process.returncode not in (0, None):
            self.status = f"异常退出（{process.returncode}）"
            details = "\n".join(output[-8:]) or "没有捕获到详细日志。"
            self.message = "服务异常退出：\n" + details
            self.show_control_page(self.message)
        elif self.process is process:
            self.status = "已停止"
            self.message = "服务已停止。"
            self.show_launch_page()

    def stop_service(self) -> dict[str, object]:
        self.stop()
        self.status = "已停止"
        self.message = "服务已停止，可以重新选择打开方式。"
        self.show_launch_page()
        return {"ok": True, "message": self.message}

    def stop(self) -> None:
        process = self.process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
        self.process = None

    def open_browser(self) -> None:
        webbrowser.open(local_url(self.port) if self.local_only else lan_url(self.port))

    def open_data_dir(self) -> None:
        path = data_dir(self.root_path)
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
        self.stop()

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

              <dl class="info-grid">
                <div><dt>电脑地址</dt><dd id="localUrl"></dd></div>
                <div><dt>手机地址</dt><dd id="lanUrl"></dd></div>
                <div><dt>数据目录</dt><dd id="dataPath"></dd></div>
              </dl>

              <p class="error" id="message"></p>
              <div class="actions">
                <button class="primary" id="startButton">启动</button>
                <button id="openDataButton">打开数据目录</button>
                <button id="openBrowserButton">在浏览器中打开</button>
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
                document.querySelector('#dataPath').textContent = state.dataPath;
                document.querySelector('#message').textContent = state.message || '';
                document.querySelector('#firstRunNotice').style.display = state.hasSavedOpenMode ? 'none' : 'block';
                refreshDerived();
              }}
              async function start() {{
                const mode = selected('openMode');
                if (!mode) {{
                  document.querySelector('#message').textContent = '首次使用请先选择打开方式。';
                  return;
                }}
                document.querySelector('#startButton').disabled = true;
                document.querySelector('#message').textContent = '正在启动……';
                const result = await window.pywebview.api.start_service({{
                  localOnly: selected('localOnly') !== 'false',
                  openMode: mode
                }});
                if (!result.ok) {{
                  document.querySelector('#message').textContent = result.message || '启动失败。';
                  document.querySelector('#startButton').disabled = false;
                }}
              }}
              render(initialState);
              document.querySelectorAll('input').forEach(input => input.addEventListener('change', refreshDerived));
              document.querySelector('#startButton').addEventListener('click', start);
              document.querySelector('#openDataButton').addEventListener('click', () => window.pywebview.api.open_data_dir());
              document.querySelector('#openBrowserButton').addEventListener('click', () => window.pywebview.api.open_browser());
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
              </dl>
              <p class="notice">{message}</p>
              <div class="actions">
                <button class="primary" onclick="window.pywebview.api.open_browser()">重新打开浏览器</button>
                <button onclick="window.pywebview.api.open_data_dir()">打开数据目录</button>
                <button onclick="window.pywebview.api.stop_service()">停止并返回设置</button>
              </div>
            </main>
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
        self.process: subprocess.Popen[str] | None = None

        saved = load_launcher_config(root_path)
        self.window = Tk()
        self.window.title(f"{APP_NAME} - Web 兜底模式")
        self.window.geometry("640x420")
        self.window.minsize(560, 360)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.local_only = BooleanVar(self.window, value=bool(saved["local_only"]))
        self.status = StringVar(self.window, value="未启动")
        self.address = StringVar(self.window)
        self.data_path = StringVar(self.window, value=str(data_dir(self.root_path)))
        self._build_ui()
        self._refresh_address()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill=BOTH, expand=True)
        ttk.Label(frame, text=APP_NAME, font=("", 18, "bold")).pack(anchor="w")
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
        info.columnconfigure(1, weight=1)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(4, 0))
        self.start_button = ttk.Button(buttons, text="浏览器打开（Web）", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止", command=self.stop, state=DISABLED)
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="打开数据目录", command=self.open_data_dir).pack(side="left")
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
            messagebox.showerror(APP_NAME, f"端口 {self.port} 已被占用，请先关闭旧服务后重试。")
            self.status.set("端口被占用")
            return
        save_launcher_config(self.root_path, local_only=bool(self.local_only.get()), open_mode="web")
        env = os.environ.copy()
        env["STUDY_DATA_DIR"] = str(data_dir(self.root_path))
        env.setdefault("STUDY_BACKUP_DIR", str(Path.home() / "Documents" / "I-Love-Learning-Backup"))
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
        self.status.set("正在启动")
        self.start_button.config(state=DISABLED)
        self.stop_button.config(state=NORMAL)
        threading.Thread(target=self._watch_process, daemon=True).start()
        self.window.after(900, self.open_browser)

    def _watch_process(self) -> None:
        process = self.process
        if process is None:
            return
        while process.poll() is None:
            self.window.after(0, lambda: self.status.set("运行中"))
            time.sleep(0.4)
        if self.process is process:
            self.window.after(0, lambda: self.status.set("已停止"))
            self.window.after(0, lambda: self.start_button.config(state=NORMAL))
            self.window.after(0, lambda: self.stop_button.config(state=DISABLED))

    def stop(self) -> None:
        process = self.process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
        self.process = None
        self.status.set("已停止")
        self.start_button.config(state=NORMAL)
        self.stop_button.config(state=DISABLED)

    def open_browser(self) -> None:
        self._refresh_address()
        webbrowser.open(self.address.get())

    def open_data_dir(self) -> None:
        path = data_dir(self.root_path)
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            webbrowser.open(path.as_uri())

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
    instance = SingleInstance(root)
    if not instance.acquire():
        messagebox.showinfo(APP_NAME, "我爱学习已经在运行，请使用已打开的启动器窗口。")
        return 0
    try:
        Launcher().run()
    finally:
        instance.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
