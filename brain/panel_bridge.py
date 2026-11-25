#!/usr/bin/env python3
"""
PanelBridge v5 — мост между пультом управления (panel/control_panel.py) и агентом.

Пульт и агент — разные процессы. Общаются через JSON-файлы:
  - data/panel_commands.json : пульт пишет команды, агент забирает и очищает
  - data/panel_status.json   : агент пишет снапшот состояния, пульт отображает

Команды пульта: start, stop, watch, train, voice, experiment <name>, report

Гонка пульт↔агент закрыта блокировкой файла (msvcrt.locking на Windows,
fcntl.lockf на остальных): без неё команда, отправленная между чтением и
очисткой очереди агентом, терялась или выполнялась дважды. Файл статуса
пишет только агент, поэтому для него достаточно атомарной замены.

Агент (в слое v5, brain/v5_runtime.py):
    from brain.panel_bridge import PanelBridge
    bridge = PanelBridge()
    for cmd, arg in bridge.poll_commands(): ...
    bridge.push_status({"mode": mode, "strength": hud["strength"], ...})
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Dict, List, Tuple

try:
    import msvcrt           # Windows
except ImportError:
    msvcrt = None
try:
    import fcntl            # POSIX
except ImportError:
    fcntl = None

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CMD_FILE = DATA_DIR / "panel_commands.json"
STATUS_FILE = DATA_DIR / "panel_status.json"


def _lock(fd) -> None:
    if msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)     # блокирует с повторами ~10 с
    elif fcntl is not None:
        fcntl.lockf(fd, fcntl.LOCK_EX)


def _unlock(fd) -> None:
    if msvcrt is not None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    elif fcntl is not None:
        fcntl.lockf(fd, fcntl.LOCK_UN)


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class PanelBridge:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.cmd_file = Path(data_dir) / "panel_commands.json"
        self.status_file = Path(data_dir) / "panel_status.json"

    def _mutate_queue(self, mutate: Callable[[List[dict]], Tuple[List[dict], list]]) -> list:
        """Читает и перезаписывает очередь команд под файловой блокировкой."""
        self.cmd_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.cmd_file, os.O_RDWR | os.O_CREAT)
        try:
            _lock(fd)
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                size = os.fstat(fd).st_size
                raw = os.read(fd, size) if size else b""
                try:
                    current = json.loads(raw.decode("utf-8")).get("queue", [])
                    if not isinstance(current, list):
                        current = []
                except Exception:
                    current = []
                new_queue, result = mutate(current)
                payload = json.dumps({"queue": new_queue}, ensure_ascii=False).encode("utf-8")
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, payload)
                os.ftruncate(fd, len(payload))
            finally:
                _unlock(fd)
        finally:
            os.close(fd)
        return result

    # ---- пульт -> агент ----
    def send_command(self, cmd: str, arg: str = "") -> None:
        entry = {"cmd": str(cmd), "arg": str(arg), "ts": time.time()}
        self._mutate_queue(lambda q: (q + [entry], []))

    def poll_commands(self) -> List[Tuple[str, str]]:
        """Агент: забрать и очистить очередь. Возвращает [(cmd, arg), ...]."""
        def take(queue: List[dict]):
            out = [(str(q.get("cmd", "")), str(q.get("arg", ""))) for q in queue]
            return [], out
        try:
            return self._mutate_queue(take)
        except OSError:
            return []

    # ---- агент -> пульт ----
    def push_status(self, status: Dict) -> None:
        payload = dict(status)
        payload["ts"] = time.time()
        _atomic_write(self.status_file, payload)

    def read_status(self) -> Dict:
        """Пульт: прочитать снапшот агента."""
        if not self.status_file.exists():
            return {}
        try:
            return json.loads(self.status_file.read_text(encoding="utf-8"))
        except Exception:
            return {}
