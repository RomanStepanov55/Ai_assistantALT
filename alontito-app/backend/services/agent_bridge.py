"""Мост управления агентом: запуск agent_boot.py (авто-запуск игры) и остановка дерева процессов."""
from __future__ import annotations
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

from backend.config import BOOT_ENTRY, PROJECT_ROOT, LOG_BUFFER_SIZE


class AgentBridge:
    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._logs = deque(maxlen=LOG_BUFFER_SIZE)
        self._started_at = None
        self._last_exit = None

    def _read_stdout(self, proc):
        try:
            for line in proc.stdout:
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                self._logs.append(f"[{ts}] {line.rstrip()}")
        except Exception:
            pass

    def is_running(self):
        return self._proc is not None and self._proc.poll() is None

    def start(self):
        with self._lock:
            if self.is_running():
                return {"ok": False, "error": "Агент уже запущен"}
            if not BOOT_ENTRY.exists():
                return {"ok": False, "error": f"Не найден {BOOT_ENTRY}"}
            self._logs.clear()
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            self._proc = subprocess.Popen(
                [sys.executable, "-u", str(BOOT_ENTRY)],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=flags, env=env,
            )
            self._started_at = time.time()
            self._last_exit = None
            threading.Thread(target=self._read_stdout, args=(self._proc,), daemon=True).start()
            return {"ok": True, "pid": self._proc.pid}

    def stop(self):
        with self._lock:
            if not self.is_running():
                return {"ok": False, "error": "Агент не запущен"}
            pid = self._proc.pid
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
            else:
                self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._last_exit = self._proc.returncode
            self._proc = None
            return {"ok": True, "exit_code": self._last_exit}

    def status(self):
        with self._lock:
            running = self.is_running()
            return {
                "running": running,
                "pid": self._proc.pid if self._proc else None,
                "uptime_s": round(time.time() - self._started_at, 1) if running and self._started_at else 0,
                "exit_code": self._proc.poll() if self._proc else None,
                "last_exit": self._last_exit,
            }

    def logs(self, limit: int = 100):
        return list(self._logs)[-limit:]


bridge = AgentBridge()