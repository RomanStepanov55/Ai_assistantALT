"""Launcher: starts the agent server (if not running) and opens the app.

Double click Alontito.bat - the app opens in a window without browser chrome
(Edge/Chrome --app mode) or in the default browser as a fallback. The server
keeps running in this console; closing the console stops the agent.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

HOST = "127.0.0.1"
PORT = 8787


def server_port_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def master_token() -> str:
    cfg_file = ROOT / "pult" / "pult_config.json"
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        return cfg.get("token") or ""
    except Exception:
        return ""


def open_app(url: str) -> None:
    """App window (no browser chrome) where possible, plain tab otherwise."""
    for exe in ("msedge", "chrome"):
        try:
            subprocess.Popen([exe, f"--app={url}"],
                             shell=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return
        except Exception:
            continue
    webbrowser.open(url)


def main() -> int:
    if not server_port_open():
        print("[АЛОНТИТО] запускаю сервер приложения…")
        subprocess.Popen([sys.executable, "-u", str(ROOT / "pult" / "server.py")],
                         cwd=str(ROOT))
        for _ in range(60):
            time.sleep(0.5)
            if server_port_open():
                break
        else:
            print("[АЛОНТИТО] сервер не поднялся за 30 с — смотри сообщения выше.")
            return 2
    token = master_token()
    url = f"http://{HOST}:{PORT}/"
    if token:
        url += "?" + urllib.parse.urlencode({"token": token})
    print(f"[АЛОНТИТО] открываю приложение: http://{HOST}:{PORT}/")
    open_app(url)
    print("[АЛОНТИТО] сервер работает в этом окне. Закройте его, чтобы остановить агента.")
    try:
        while server_port_open():
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
