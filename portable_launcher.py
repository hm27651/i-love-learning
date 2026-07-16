from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import BOTH, DISABLED, NORMAL, BooleanVar, StringVar, Tk, ttk, messagebox


APP_NAME = "我爱学习"
DEFAULT_PORT = 23456


def portable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir(root: Path | None = None) -> Path:
    return (root or portable_root()) / "data"


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
    ip = lan_ip() or "本机局域网IP"
    return f"http://{ip}:{port}"


def is_port_available(host: str, port: int = DEFAULT_PORT) -> bool:
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((probe_host, port)) != 0


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


class Launcher:
    def __init__(self) -> None:
        self.root_path = portable_root()
        self.port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
        self.process: subprocess.Popen[str] | None = None

        self.window = Tk()
        self.window.title(APP_NAME)
        self.window.geometry("520x300")
        self.window.minsize(460, 280)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.local_only = BooleanVar(self.window, value=True)
        self.status = StringVar(self.window, value="未启动")
        self.address = StringVar(self.window, value=local_url(self.port))
        self._build_ui()
        self._refresh_address()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.window, padding=18)
        frame.pack(fill=BOTH, expand=True)

        title = ttk.Label(frame, text=APP_NAME, font=("", 18, "bold"))
        title.pack(anchor="w")

        mode = ttk.LabelFrame(frame, text="访问模式", padding=12)
        mode.pack(fill="x", pady=(16, 10))
        ttk.Radiobutton(mode, text="仅本机访问", variable=self.local_only, value=True, command=self._refresh_address).pack(anchor="w")
        ttk.Radiobutton(mode, text="允许同一局域网访问", variable=self.local_only, value=False, command=self._refresh_address).pack(anchor="w", pady=(6, 0))

        info = ttk.Frame(frame)
        info.pack(fill="x", pady=(4, 12))
        ttk.Label(info, text="状态").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Label(info, textvariable=self.status).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(info, text="地址").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=3)
        ttk.Label(info, textvariable=self.address).grid(row=1, column=1, sticky="w", pady=3)
        info.columnconfigure(1, weight=1)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(4, 0))
        self.start_button = ttk.Button(buttons, text="启动", command=self.start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止", command=self.stop, state=DISABLED)
        self.stop_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(buttons, text="打开浏览器", command=self.open_browser, state=DISABLED)
        self.open_button.pack(side="left")
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
            messagebox.showerror(APP_NAME, f"端口 {self.port} 已被占用，请先关闭占用该端口的程序。")
            return

        env = os.environ.copy()
        env["STUDY_DATA_DIR"] = str(data_dir(self.root_path))
        env.setdefault("STUDY_BACKUP_DIR", str(Path.home() / "Documents" / "I-Love-Learning-Backup"))
        env["PORT"] = str(self.port)
        command = [sys.executable, "--serve", "--host", host, "--port", str(self.port)]
        if not getattr(sys, "frozen", False):
            command = [sys.executable, str(Path(__file__).resolve()), "--serve", "--host", host, "--port", str(self.port)]

        try:
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
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"启动失败：{exc}")
            return

        self.status.set("正在启动")
        self.start_button.config(state=DISABLED)
        self.stop_button.config(state=NORMAL)
        self.open_button.config(state=NORMAL)
        threading.Thread(target=self._watch_process, daemon=True).start()
        self.window.after(900, self.open_browser)

    def _watch_process(self) -> None:
        output: list[str] = []
        assert self.process is not None
        while True:
            line = self.process.stdout.readline() if self.process.stdout else ""
            if line:
                output.append(line.rstrip())
                if len(output) > 12:
                    output = output[-12:]
            if self.process.poll() is not None:
                break
            if self.status.get() == "正在启动":
                self.status.set("运行中")
            time.sleep(0.1)
        code = self.process.returncode
        self.window.after(0, lambda: self._process_stopped(code, output))

    def _process_stopped(self, code: int | None, output: list[str]) -> None:
        self.start_button.config(state=NORMAL)
        self.stop_button.config(state=DISABLED)
        self.open_button.config(state=DISABLED)
        self.status.set("已停止" if code in (0, None) else f"异常退出（{code}）")
        if code not in (0, None):
            messagebox.showerror(APP_NAME, "服务异常退出：\n" + "\n".join(output[-8:]))

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        self.status.set("已停止")
        self.start_button.config(state=NORMAL)
        self.stop_button.config(state=DISABLED)
        self.open_button.config(state=DISABLED)

    def open_browser(self) -> None:
        self._refresh_address()
        webbrowser.open(self.address.get())

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
