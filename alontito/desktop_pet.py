#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alontito/desktop_pet.py — интерактивный 3D Desktop Pet (маскот поверх рабочего стола).

Архитектура
-----------
Терминал Alontito (app.py) блокируется на вводе, а Qt обязан жить в главном потоке процесса,
поэтому питомец работает в ОТДЕЛЬНОМ процессе:

    app.py ── PetController ──(stdin: JSON-строки)──►  desktop_pet.py --child  (Qt + QWebEngine + Three.js)
                            ◄─(stdout: @@PETEV@@ JSON)─

* PetController   — то, что импортирует терминал: start()/stop()/send()/status (см. pet_terminal.py).
* Дочерний процесс — прозрачное окно без рамок (Frameless + StaysOnTop + TranslucentBackground),
  внутри QWebEngineView с Three.js (alontito/pet3d/pet.js), который рисует .glb и играет клипы.
* Drag & Drop: ЛКМ по персонажу → drag_panic и следование за курсором; отпустил → гравитация
  к нижней кромке экрана (availableGeometry, т.е. над панелью задач) → drag_drop.
* Испарение (disintegrate) — шейдер растворения + частицы при скрытии/выключении.
* Трекер активности: Win32 GetForegroundWindow → IDE = activity_coding; периодически
  персонаж «достаёт предметы из портала» (portal_reach_in → portal_eat_drink).

Зависимости: PyQt6 + PyQt6-WebEngine (или PySide6). Three.js r160 скачивается при первом запуске в
alontito/pet3d/vendor/three (или берётся с CDN, если нет сети).

Ручной запуск без терминала:   python alontito/desktop_pet.py --standalone
Диагностика:                    python alontito/desktop_pet.py --check
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import random
import subprocess
import sys
import threading
import time
import types
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "pet3d"
ASSETS_DIR = HERE / "assets" / "3d"
CONFIG_PATH = HERE / "pet_config.json"
FALLBACK_CONFIG_PATH = Path.home() / ".alontito" / "pet_config.json"
EVENT_PREFIX = "@@PETEV@@ "
THREE_VERSION = "0.160.0"

MODES = ("auto", "free", "still")
MODE_HELP = {
    "auto": "реагирует на активность: IDE → кодит, простой → живёт своей жизнью",
    "free": "живёт своей жизнью: гуляет, сидит, крутит кубик Рубика, игнорирует окна",
    "still": "спокойный: стоит/сидит на месте, не гуляет и не отвлекается",
}
ALL_CLIPS = ("idle", "walk", "sit_idle", "drag_panic", "drag_drop", "activity_coding", "activity_rubik",
             "portal_reach_in", "portal_eat_drink", "disintegrate",
             "run", "tiptoe", "happy", "laugh", "excited", "sad", "cry", "angry", "annoyed", "surprised", "confused", "curious", "shy", "scared", "proud", "facepalm", "shrug", "clap", "dance", "bow", "point", "sneeze", "cough",
             # v4 (tools/build_pet_clips_v4.py): 16 новых клипов
             "wave", "nod", "shake_head", "sleepy", "fall", "land", "backflip",
             "spin_360", "moonwalk", "stretch", "yawn", "think", "idea", "dab",
             "peek", "headbang")

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,            # включён ли 3D-агент (запускать вместе с терминалом)
    "asked": False,              # задан ли вопрос «Хочешь включить живого 3D-ассистента?»
    "mode": "auto",
    "scale": 0.65,               # 1.0 ≈ персонаж высотой 310 px; 0.65 ≈ 200 px — комфортный размер и меньше GPU
    "fps": 45,                   # верхний предел FPS; спокойные состояния ограничены ниже
    "gpu": "auto",               # "auto" | "software" (если WebGL чёрный/не работает)
    "character": "",             # путь к .glb персонажа (пусто = искать в alontito/assets/3d)
    "portal": "",                # путь к .glb портала
    "walk_speed": 95,            # px/с
    "park_x": 0.82,              # где садится кодить (доля ширины экрана)
    "portal_interval": [70, 140],  # пауза между «заходами в портал», сек
    "ide_exes": [
        "code.exe", "code - insiders.exe", "cursor.exe", "windsurf.exe", "vscodium.exe", "pycharm64.exe", "pycharm.exe",
        "idea64.exe", "webstorm64.exe", "clion64.exe", "rider64.exe", "goland64.exe", "phpstorm64.exe", "rubymine64.exe",
        "datagrip64.exe", "devenv.exe", "sublime_text.exe", "atom.exe", "zed.exe", "studio64.exe",
        "eclipse.exe", "kate.exe", "gvim.exe", "nvim-qt.exe",
    ],
    "ide_title_hints": ["visual studio code", "pycharm", "intellij idea", "sublime text", "cursor"],
}


# ====================================================================================== конфиг
def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for p in ([path] if path else [CONFIG_PATH, FALLBACK_CONFIG_PATH]):
        try:
            if p and p.is_file():
                cfg.update(json.loads(p.read_text(encoding="utf-8")))
                break
        except (OSError, ValueError):
            continue
    if cfg.get("mode") not in MODES:
        cfg["mode"] = "auto"
    return cfg


def save_config(cfg: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Атомарно пишет конфиг; если папка проекта недоступна на запись — в ~/.alontito."""
    data = json.dumps(cfg, ensure_ascii=False, indent=2)
    last_err: Optional[Exception] = None
    for p in ([path] if path else [CONFIG_PATH, FALLBACK_CONFIG_PATH]):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, p)
            return p
        except OSError as e:
            last_err = e
    raise OSError(f"не удалось сохранить конфиг: {last_err}")


# ====================================================================================== ассеты и three.js
def find_asset(kind: str, cfg: Dict[str, Any]) -> Optional[Path]:
    """kind: 'character' | 'portal'. Ищет явный путь из конфига, затем alontito/assets/3d и соседние папки."""
    explicit = cfg.get(kind) or ""
    if explicit and Path(explicit).expanduser().is_file():
        return Path(explicit).expanduser()
    patterns = {
        "character": ["hooded_neon_character_rigged.glb", "*rigged*.glb", "*character*.glb"],
        "portal": ["mini_portal_slim.glb", "*portal*.glb"],
    }[kind]
    dirs = [ASSETS_DIR, HERE / "3d", HERE.parent / "assets" / "3d", HERE / "assets", HERE, HERE.parent]
    for d in dirs:
        if not d.is_dir():
            continue
        for pat in patterns:
            hits = sorted(d.glob(pat))
            if hits:
                return hits[0]
    if kind == "character":                       # последний шанс: любой .glb, кроме портала
        for d in dirs[:3]:
            for f in sorted(d.glob("*.glb")) if d.is_dir() else []:
                if "portal" not in f.name.lower():
                    return f
    return None


VENDOR_DIR = WEB_DIR / "vendor" / "three"
_CDN = f"https://cdn.jsdelivr.net/npm/three@{THREE_VERSION}"
VENDOR_FILES = {
    "three.module.js": f"{_CDN}/build/three.module.js",
    "loaders/GLTFLoader.js": f"{_CDN}/examples/jsm/loaders/GLTFLoader.js",
    "utils/BufferGeometryUtils.js": f"{_CDN}/examples/jsm/utils/BufferGeometryUtils.js",
}


def vendor_ready() -> bool:
    return all((VENDOR_DIR / rel).is_file() and (VENDOR_DIR / rel).stat().st_size > 1000 for rel in VENDOR_FILES)


def ensure_vendor(log: Callable[[str], None] = lambda s: None, timeout: float = 40.0) -> bool:
    """Скачивает three.js (3 файла, ~1.3 МБ) в pet3d/vendor/three. Возвращает True, если всё на месте."""
    if vendor_ready():
        return True
    for rel, url in VENDOR_FILES.items():
        dst = VENDOR_DIR / rel
        if dst.is_file() and dst.stat().st_size > 1000:
            continue
        try:
            log(f"скачиваю {rel} …")
            dst.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": "alontito-desktop-pet"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) < 1000 or b"<html" in data[:200].lower():
                raise ValueError("получен не JS")
            tmp = dst.with_suffix(dst.suffix + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dst)
        except Exception as e:                                   # noqa: BLE001 — сеть может падать как угодно
            log(f"не удалось скачать {rel}: {e}")
            return False
    return vendor_ready()


def import_map(local: bool) -> str:
    if local:
        m = {"imports": {"three": "/vendor/three/three.module.js", "three/addons/": "/vendor/three/"}}
    else:
        m = {"imports": {"three": f"{_CDN}/build/three.module.js", "three/addons/": f"{_CDN}/examples/jsm/"}}
    return json.dumps(m)


# ====================================================================================== локальный HTTP-сервер
_MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
}  # свой словарь: на Windows mimetypes берёт .js из реестра и иногда отдаёт text/plain — модули тогда не грузятся


class PetServer:
    """Раздаёт pet3d/ и два .glb только на 127.0.0.1 (порт выбирается системой)."""

    def __init__(self, web_dir: Path, assets: Dict[str, Optional[Path]], use_vendor: bool):
        self.web_dir = web_dir.resolve()
        self.assets = {k: v for k, v in assets.items() if v}
        self.use_vendor = use_vendor
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.port = 0

    def start(self) -> int:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "AlontitoPet/1.0"

            def log_message(self, *a):           # тишина в консоли терминала
                pass

            def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-cache"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", cache)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                host = (self.headers.get("Host") or "").split(":")[0].lower()
                if host not in ("127.0.0.1", "localhost"):          # защита от DNS-rebinding
                    return self._send(403, b"forbidden", "text/plain")
                path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
                if path in ("/", "/index.html"):
                    html = (outer.web_dir / "index.html").read_text(encoding="utf-8")
                    html = html.replace("{{IMPORTMAP}}", import_map(outer.use_vendor))
                    return self._send(200, html.encode("utf-8"), _MIME[".html"])
                if path.startswith("/asset/"):
                    name = path[len("/asset/"):]
                    key = {"character.glb": "character", "portal.glb": "portal"}.get(name)
                    f = outer.assets.get(key) if key else None
                    if not f or not f.is_file():
                        return self._send(404, b"no asset", "text/plain")
                    return self._send(200, f.read_bytes(), _MIME[".glb"], cache="max-age=3600")
                f = (outer.web_dir / path.lstrip("/")).resolve()
                try:
                    f.relative_to(outer.web_dir)
                except ValueError:
                    return self._send(403, b"forbidden", "text/plain")
                if not f.is_file():
                    return self._send(404, b"not found", "text/plain")
                return self._send(200, f.read_bytes(), _MIME.get(f.suffix.lower(), "application/octet-stream"))

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, name="pet-http", daemon=True).start()
        return self.port

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


# ====================================================================================== трекер активности (Win32)
@dataclass
class Activity:
    kind: str = "other"           # 'coding' | 'other'
    exe: str = ""
    title: str = ""
    user_idle: float = 0.0        # секунд без ввода


def classify(exe: str, title: str, cfg: Dict[str, Any]) -> str:
    """Чистая функция: по имени процесса и заголовку окна определяет вид активности."""
    e, t = (exe or "").lower(), (title or "").strip().lower()
    if e in set(x.lower() for x in cfg.get("ide_exes", [])):
        return "coding"
    if e and any(h in t for h in cfg.get("ide_title_hints", [])) and e not in ("chrome.exe", "msedge.exe", "firefox.exe"):
        return "coding"
    return "other"


class ActivityTracker:
    """Опрос активного окна через Win32: GetForegroundWindow → процесс/заголовок; GetLastInputInfo → простой."""

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self._win = None
        if sys.platform == "win32":
            try:
                self._win = self._init_win32()
            except Exception:                                    # noqa: BLE001
                self._win = None

    @staticmethod
    def _init_win32():
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.GetTickCount.restype = wintypes.DWORD

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        return types.SimpleNamespace(ctypes=ctypes, wintypes=wintypes, user32=user32, kernel32=kernel32, LII=LASTINPUTINFO)

    def _foreground(self) -> tuple:
        w = self._win
        ct, wt = w.ctypes, w.wintypes
        hwnd = w.user32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        n = w.user32.GetWindowTextLengthW(hwnd)
        buf = ct.create_unicode_buffer(n + 1)
        w.user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        pid = wt.DWORD(0)
        w.user32.GetWindowThreadProcessId(hwnd, ct.byref(pid))
        if pid.value == os.getpid():
            return "", ""                                         # это окно самого питомца
        exe = ""
        h = w.kernel32.OpenProcess(0x1000, False, pid.value)      # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            try:
                size = wt.DWORD(1024)
                pbuf = ct.create_unicode_buffer(1024)
                if w.kernel32.QueryFullProcessImageNameW(h, 0, pbuf, ct.byref(size)):
                    exe = os.path.basename(pbuf.value)
            finally:
                w.kernel32.CloseHandle(h)
        return exe, title

    def _idle_seconds(self) -> float:
        w = self._win
        info = w.LII()
        info.cbSize = w.ctypes.sizeof(info)
        if not w.user32.GetLastInputInfo(w.ctypes.byref(info)):
            return 0.0
        return ((w.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0

    def sample(self) -> Activity:
        fake = os.environ.get("ALONTITO_PET_FAKE_ACTIVITY")      # для отладки: coding | other
        if fake:
            return Activity(kind=fake, exe="fake", title="fake")
        if not self._win:
            return Activity()
        try:
            exe, title = self._foreground()
            return Activity(kind=classify(exe, title, self.cfg), exe=exe, title=title, user_idle=self._idle_seconds())
        except Exception:                                         # noqa: BLE001 — Win32 не должен ронять питомца
            return Activity()


# ====================================================================================== «мозг»: чистая логика поведения
def cmd(type_: str, **kw) -> Dict[str, Any]:
    kw["type"] = type_
    return kw


STATE_VIEW = {            # состояние -> (клип, поворот в градусах: 0 к пользователю)
    "idle": ("idle", 0), "sit": ("sit_idle", 0), "coding": ("activity_coding", 14),
    "rubik": ("activity_rubik", 20),
}
CALM_FPS, ACTIVE_FPS = 20, 45   # спокойные состояния 20 FPS, активные до 45 — заметно снижает нагрузку
FACE_FOR_CLIP = {
    "walk": 90, "run": 90, "tiptoe": 90,
    "activity_coding": 14, "activity_rubik": 20,
    "happy": 0, "laugh": 0, "excited": 0, "sad": 0, "cry": 0, "angry": 0,
    "annoyed": 0, "surprised": 0, "confused": 0, "curious": 10, "shy": -8,
    "scared": 0, "proud": 0, "facepalm": 0, "shrug": 0, "clap": 0, "dance": 0,
    "bow": 0, "point": 20, "sneeze": 0, "cough": 0,
}


class PetBrain:
    """
    Состояния: hidden → materializing → idle ⇄ walk / sit / rubik / coding ⇄ portal;
    drag → fall → land → idle; dissolving → hidden; manual (проиграть клип вручную).
    Не знает про Qt: принимает события/контекст, возвращает список команд (dict) для окна/рендерера.
    """

    def __init__(self, cfg: Dict[str, Any], rng: Optional[random.Random] = None, now: float = 0.0):
        self.cfg = cfg
        self.rng = rng or random.Random()
        self.mode = cfg.get("mode", "auto")
        self.state = "hidden"
        self.since = now
        self.until: Optional[float] = None
        self.act_kind, self.act_since = "other", now
        self.after_walk: Optional[str] = None
        self.prev_state = "idle"
        self.next_portal = now + self._portal_delay()
        self.portal_enabled = True
        self._fps = -1

    # ---------------------------------------------------------------- утилиты
    def _portal_delay(self) -> float:
        lo, hi = self.cfg.get("portal_interval", [70, 140])
        return self.rng.uniform(lo, hi)

    def _enter(self, state: str, now: float, out: list, hold: Optional[float] = None) -> None:
        self.state, self.since = state, now
        self.until = now + hold if hold else None
        if state in STATE_VIEW:
            clip, deg = STATE_VIEW[state]
            out += [cmd("play", clip=clip), cmd("face", deg=deg)]
        if state == "idle" and hold is None:
            self.until = now + self.rng.uniform(5, 12)
        elif state == "sit" and hold is None:
            self.until = now + self.rng.uniform(15, 35)
        elif state == "rubik" and hold is None:
            self.until = now + self.rng.uniform(18, 40)

    def _start_portal(self, now: float, out: list, force: bool = False) -> bool:
        if not self.portal_enabled and not force:
            return False
        self.prev_state = self.state if self.state in STATE_VIEW else "idle"
        self.state, self.since, self.until = "portal", now, None
        self.next_portal = now + self._portal_delay()
        out += [cmd("stop_walk"), cmd("portal", action="run")]
        return True

    def _start_walk(self, goal_x: float, now: float, out: list, after: Optional[str], cx: float) -> None:
        self.state, self.since, self.until, self.after_walk = "walk", now, None, after
        out += [cmd("face", deg=90 if goal_x > cx else -90), cmd("play", clip="walk"), cmd("walk_to", x=goal_x, speed=self.cfg.get("walk_speed", 95))]

    def _fps_cmd(self, out: list) -> None:
        fps = ACTIVE_FPS if self.state in ("walk", "drag", "fall", "land", "portal", "dissolving", "materializing", "manual") else CALM_FPS
        fps = min(fps, int(self.cfg.get("fps", 60)))
        if fps != self._fps:
            self._fps = fps
            out.append(cmd("fps", value=fps))

    # ---------------------------------------------------------------- внешние команды
    def set_mode(self, mode: str, now: float) -> list:
        out: list = []
        if mode in MODES:
            self.mode = mode
            if self.state == "coding" and mode != "auto":
                self._enter("idle", now, out)
            if self.state == "walk" and mode == "still":
                out.append(cmd("stop_walk"))
                self._enter("idle", now, out)
        return out

    def hide(self, now: float) -> list:
        out: list = []
        if self.state in ("hidden", "dissolving"):
            return out
        self.state, self.since, self.until = "dissolving", now, None
        out += [cmd("stop_walk"), cmd("portal", action="cancel"), cmd("dissolve", dir="out")]
        return out

    def show(self, now: float) -> list:
        out: list = []
        if self.state not in ("hidden",):
            return out
        self.state, self.since = "materializing", now
        out += [cmd("show_window"), cmd("dissolve", dir="in")]
        return out

    def play_clip(self, name: str, now: float, dur: float = 5.0) -> list:
        out: list = []
        if self.state in ("hidden", "dissolving", "materializing", "drag", "fall"):
            return out
        if name == "portal_reach_in" or name == "portal_eat_drink":
            self._start_portal(now, out, force=True)
            return out
        if name == "disintegrate":
            return self.hide(now)
        out.append(cmd("stop_walk"))
        self.state, self.since, self.until = "manual", now, now + dur
        loop = name not in ("drag_drop",)
        out += [cmd("face", deg=FACE_FOR_CLIP.get(name, 0)), cmd("play", clip=name, loop=loop)]
        return out

    def portal_now(self, now: float) -> list:
        out: list = []
        if self.state in ("idle", "sit", "rubik", "coding", "walk", "manual"):
            self._start_portal(now, out, force=True)
        return out

    # ---------------------------------------------------------------- события от окна/рендерера
    def on_event(self, ev: Dict[str, Any], now: float) -> list:
        out: list = []
        e = ev.get("ev")
        if e == "grab":
            if self.state in ("hidden", "dissolving", "materializing"):
                return out
            out += [cmd("stop_walk"), cmd("portal", action="cancel"), cmd("face", deg=0, fast=False), cmd("play", clip="drag_panic", fade=0.1), cmd("begin_drag")]
            self.state, self.since, self.until = "drag", now, None
        elif e == "release":
            if self.state == "drag":
                self.state, self.since = "fall", now
                out.append(cmd("end_drag"))
        elif e == "landed":
            if self.state == "fall":
                self.state, self.since = "land", now
                out += [cmd("play", clip="drag_drop", loop=False, fade=0.08)]
        elif e == "clip_end":
            if self.state == "land" and ev.get("name") == "drag_drop":
                self._enter("idle", now, out, hold=self.rng.uniform(2, 5))
        elif e == "walk_done":
            if self.state == "walk":
                nxt, self.after_walk = self.after_walk, None
                if nxt == "coding":
                    self._enter(nxt, now, out)
                else:
                    self._enter("idle", now, out, hold=self.rng.uniform(3, 8))
        elif e == "portal_done":
            if self.state == "portal":
                back = self.prev_state if self.prev_state in STATE_VIEW else "idle"
                self._enter(back, now, out)
        elif e == "dissolve_done":
            if ev.get("dir") == "out" and self.state == "dissolving":
                self.state, self.since = "hidden", now
                out.append(cmd("hide_window"))
            elif ev.get("dir") == "in" and self.state == "materializing":
                self._enter("idle", now, out)
        elif e == "dblclick":
            out += self.portal_now(now)
        self._fps_cmd(out)
        return out

    # ---------------------------------------------------------------- такт (≈10 Гц)
    def tick(self, now: float, ctx: Dict[str, Any]) -> list:
        """ctx: activity ('coding'|'other'), user_idle (с), x (центр персонажа, px), xmin, xmax, park_x, portal (bool)."""
        out: list = []
        self.portal_enabled = bool(ctx.get("portal", True))
        act = ctx.get("activity", "other")
        if act != self.act_kind:
            self.act_kind, self.act_since = act, now
        stable = now - self.act_since
        st = self.state
        idle_s = float(ctx.get("user_idle", 0.0))

        if st in ("hidden", "dissolving", "materializing", "drag", "fall"):
            return out
        if st == "land":
            if now - self.since > 2.0:
                self._enter("idle", now, out)
        elif st == "manual":
            if self.until is not None and now >= self.until:
                self._enter("idle", now, out)
        elif st == "portal":
            if now - self.since > 14.0:                          # страховка: рендерер не ответил
                out.append(cmd("portal", action="cancel"))
                self._enter(self.prev_state if self.prev_state in STATE_VIEW else "idle", now, out)
        else:
            # --- реакция на активность пользователя (только режим auto)
            want = act if (self.mode == "auto" and act == "coding" and stable >= 2.5) else None
            walking_to = self.after_walk if st == "walk" else None
            if want and st != want and walking_to != want:
                px, cx = float(ctx.get("park_x", ctx.get("x", 0))), float(ctx.get("x", 0))
                if abs(px - cx) > 70:
                    self._start_walk(px, now, out, after=want, cx=cx)
                else:
                    self._enter(want, now, out)
            elif st == "coding" and (self.mode != "auto" or (act != st and stable >= 5.0)):
                self._enter("idle", now, out)
            elif st == "walk":
                if now - self.since > 45:
                    out.append(cmd("stop_walk"))
                    self._enter("idle", now, out)
            elif st == "idle":
                if self.until is None:
                    self.until = now + self.rng.uniform(5, 12)
                if now >= self.until:
                    self._choose_idle(now, ctx, idle_s, out)
            elif st in ("sit", "rubik"):
                if self.until is not None and now >= self.until:
                    self._enter("idle", now, out)
        self._fps_cmd(out)
        return out

    def _choose_idle(self, now: float, ctx: Dict[str, Any], idle_s: float, out: list) -> None:
        # Базовые действия + лёгкие эмоции, чтобы персонаж «жил» и реагировал на простой
        opts: List[tuple] = [
            ("stay", 1.8),
            ("sit", 5.5 if idle_s >= 60 else 2.8),
            ("emotion", 2.2),  # короткие эмоциональные клипы
        ]
        if self.mode != "still":
            opts.append(("walk", 4.5))
            if idle_s >= 40 or self.mode == "free":
                opts.append(("rubik", 5.5 if idle_s >= 90 else 2.8))
            if self.portal_enabled and now >= self.next_portal:
                opts.append(("portal", 2.8))
            if idle_s >= 25 and self.mode == "free":
                opts.append(("dance", 1.2))
        pick = self.rng.choices([o[0] for o in opts], weights=[o[1] for o in opts])[0]
        cx = float(ctx.get("x", 0))
        if pick == "walk":
            lo, hi = float(ctx.get("xmin", cx - 300)), float(ctx.get("xmax", cx + 300))
            for _ in range(8):
                gx = self.rng.uniform(lo, hi)
                if abs(gx - cx) >= 140:
                    break
            # иногда бег вместо ходьбы
            if abs(gx - cx) > 280 and self.rng.random() < 0.35:
                self.state, self.since, self.until, self.after_walk = "walk", now, None, None
                out += [cmd("face", deg=90 if gx > cx else -90), cmd("play", clip="run"), cmd("walk_to", x=gx, speed=self.cfg.get("walk_speed", 95) * 1.55)]
            else:
                self._start_walk(gx, now, out, after=None, cx=cx)
        elif pick == "sit":
            self._enter("sit", now, out)
        elif pick == "rubik":
            self._enter("rubik", now, out)
        elif pick == "portal":
            self._start_portal(now, out)
        elif pick == "dance":
            self.state, self.since, self.until = "manual", now, now + 2.6
            out += [cmd("stop_walk"), cmd("face", deg=0), cmd("play", clip="dance", loop=False)]
        elif pick == "emotion":
            # подбор эмоции по длительности простоя и режиму
            if idle_s >= 120:
                pool = ["sleepy" if False else "sit_idle", "sad", "shy", "curious", "facepalm", "shrug"]
            elif idle_s >= 45:
                pool = ["curious", "happy", "confused", "proud", "point", "clap"]
            else:
                pool = ["happy", "curious", "surprised", "shrug", "clap", "excited"]
            # sit_idle уже есть; убираем несуществующие
            pool = [c for c in pool if c in ALL_CLIPS or c == "sit_idle"]
            if not pool:
                pool = ["happy", "curious", "shrug"]
            clip = self.rng.choice(pool)
            if clip == "sit_idle":
                self._enter("sit", now, out)
            else:
                self.state, self.since, self.until = "manual", now, now + 2.0
                out += [cmd("stop_walk"), cmd("face", deg=FACE_FOR_CLIP.get(clip, 0)), cmd("play", clip=clip, loop=False)]
        else:
            self._enter("idle", now, out)


# ====================================================================================== протокол родитель ↔ ребёнок
def emit_event(**kw) -> None:
    """Ребёнок → родитель: одна JSON-строка в stdout."""
    try:
        sys.stdout.write(EVENT_PREFIX + json.dumps(kw, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


class PetController:
    """
    Фасад для терминала. Запускает дочерний процесс питомца и следит за его состоянием.

    status: 'off' | 'starting' | 'on' | 'hiding' | 'error'
    """

    def __init__(self, cfg: Optional[Dict[str, Any]] = None, python: Optional[str] = None,
                 child_argv: Optional[List[str]] = None):
        self.cfg = cfg if cfg is not None else load_config()
        self.python = python or sys.executable
        self.child_argv = child_argv or [self.python, str(Path(__file__).resolve()), "--child"]
        self.proc: Optional[subprocess.Popen] = None
        self.status = "off"
        self.detail = ""
        self.mode = self.cfg.get("mode", "auto")
        self.pet_state = ""
        self.activity = ""
        self.log: List[str] = []
        self._lock = threading.Lock()
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []

    # --- подписка (например, чтобы перерисовать шапку терминала)
    def on_change(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def _notify(self, ev: Dict[str, Any]) -> None:
        for fn in list(self._listeners):
            try:
                fn(ev)
            except Exception:                                    # noqa: BLE001
                pass

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return True
            self.status, self.detail = "starting", "запуск…"
            try:
                kw: Dict[str, Any] = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          text=True, encoding="utf-8", errors="replace", bufsize=1)
                if sys.platform == "win32":
                    kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self.proc = subprocess.Popen(self.child_argv, **kw)
            except OSError as e:
                self.status, self.detail = "error", f"не удалось запустить: {e}"
                self._notify({"ev": "error", "msg": self.detail})
                return False
        threading.Thread(target=self._read_stdout, args=(self.proc,), name="pet-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self.proc,), name="pet-err", daemon=True).start()
        self._notify({"ev": "starting"})
        return True

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:                              # type: ignore[union-attr]
                if line.startswith(EVENT_PREFIX):
                    try:
                        self._handle(json.loads(line[len(EVENT_PREFIX):]))
                    except ValueError:
                        pass
        finally:
            rc = proc.wait()
            if proc is self.proc:
                if self.status not in ("error",):
                    self.status = "off" if rc == 0 or self.status == "hiding" else "error"
                    if self.status == "error" and not self.detail:
                        self.detail = f"процесс завершился с кодом {rc}"
                self._notify({"ev": "exit", "code": rc})

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr:                                  # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                self.log.append(line)
                del self.log[:-200]

    def _handle(self, ev: Dict[str, Any]) -> None:
        e = ev.get("ev")
        if e == "ready":
            self.status, self.detail = "on", ""
        elif e == "status":
            self.pet_state, self.activity = ev.get("state", ""), ev.get("activity", "")
            self.mode = ev.get("mode", self.mode)
        elif e == "error":
            self.status, self.detail = "error", str(ev.get("msg", "ошибка"))[:300]
        elif e == "info":
            self.detail = str(ev.get("msg", ""))[:200]
        self._notify(ev)

    def send(self, **msg: Any) -> bool:
        p = self.proc
        if not p or p.poll() is not None or not p.stdin:
            return False
        try:
            p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            p.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def set_mode(self, mode: str) -> bool:
        if mode not in MODES:
            return False
        self.mode = mode
        return self.send(cmd="mode", value=mode)

    def stop(self, timeout: float = 5.0) -> None:
        """Мягко: испарение → выход. Если не успел — terminate/kill."""
        p = self.proc
        if not p:
            self.status = "off"
            return
        if p.poll() is None:
            self.status = "hiding"
            self.send(cmd="quit")
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    p.kill()
        self.status, self.detail, self.pet_state = "off", "", ""
        self._notify({"ev": "exit", "code": 0})


# ====================================================================================== Qt-часть (дочерний процесс)
BASE_W, BASE_H = 540, 400          # размер окна при scale = 1.0 (персонаж ≈ 310 px высотой)
FLOOR_PAD = 2                      # ступни чуть выше панели задач
GRAVITY = 3200.0                   # px/с² при scale = 1.0
DRAG_START_PX, DRAG_START_S = 6, 0.18


def _load_qt() -> types.SimpleNamespace:
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets, QtWebEngineCore, QtWebEngineWidgets
        name = "PyQt6"
    except ImportError:
        try:
            from PySide6 import QtCore, QtGui, QtWidgets, QtWebEngineCore, QtWebEngineWidgets
            name = "PySide6"
        except ImportError as e:
            raise RuntimeError("Не найден PyQt6/PySide6 с WebEngine. Установи:  pip install PyQt6 PyQt6-WebEngine") from e
    return types.SimpleNamespace(name=name, QtCore=QtCore, QtGui=QtGui, QtWidgets=QtWidgets,
                                 QtWebEngineCore=QtWebEngineCore, QtWebEngineWidgets=QtWebEngineWidgets)


def _build_window_class(Q: types.SimpleNamespace):
    QtCore, QtGui, QtWidgets, Core, Web = Q.QtCore, Q.QtGui, Q.QtWidgets, Q.QtWebEngineCore, Q.QtWebEngineWidgets
    Qt = QtCore.Qt

    class PetPage(Core.QWebEnginePage):
        """Страница, которая принимает сообщения рендерера через console.log('@@PET@@{json}')."""

        def __init__(self, parent, on_msg, debug):
            super().__init__(parent)
            self._on_msg, self._debug = on_msg, debug

        def javaScriptConsoleMessage(self, level, message, line, source):       # noqa: N802 (Qt API)
            if message.startswith("@@PET@@"):
                try:
                    self._on_msg(json.loads(message[7:]))
                except ValueError:
                    pass
            elif self._debug:
                sys.stderr.write(f"[js] {message}  ({source}:{line})\n")

    class PetWindow(QtWidgets.QWidget):
        def __init__(self, cfg, port, has_portal, tracker, brain, debug=False, standalone=False):
            flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                     | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
            super().__init__(None, flags)
            self.cfg, self.tracker, self.brain = cfg, tracker, brain
            self.has_portal, self.debug, self.standalone = has_portal, debug, standalone
            self.scale = float(cfg.get("scale", 0.8))
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setWindowTitle("Alontito 3D Pet")
            self._apply_size()

            self.view = Web.QWebEngineView(self)
            self.page = PetPage(self.view, self._on_js, debug)
            self.view.setPage(self.page)
            self.page.setBackgroundColor(QtGui.QColor(0, 0, 0, 0))
            self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.view.setStyleSheet("background: transparent;")
            self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            st = self.page.settings()
            WA = Core.QWebEngineSettings.WebAttribute
            for attr, val in ((WA.WebGLEnabled, True), (WA.Accelerated2dCanvasEnabled, True), (WA.ShowScrollBars, False),
                              (WA.JavascriptCanOpenWindows, False), (WA.PlaybackRequiresUserGesture, False)):
                st.setAttribute(attr, val)
            self.page.renderProcessTerminated.connect(self._on_render_died)
            q = urllib.parse.urlencode({"char": "/asset/character.glb", "fps": cfg.get("fps", 60),
                                        **({"portal": "/asset/portal.glb"} if has_portal else {})})
            self.view.load(QtCore.QUrl(f"http://127.0.0.1:{port}/?{q}"))

            # геометрия/раскладка (обновляется событием layout из JS)
            self.lay = {"feet_y": 0.9 * self.height(), "center_x": 0.36 * self.width(), "ppm": 300.0}
            self.rects: List[List[float]] = []
            self.fx = self.fy = 0.0
            self._anchor_cx: Optional[float] = None
            self._vx = self._vy = 0.0
            self._drag = self._fall = self._bounced = False
            self._pending_grab: Optional[tuple] = None
            self._grab_dx = self._grab_dy = 0.0
            self._trail: List[tuple] = []
            self._walk_goal: Optional[float] = None
            self._walk_speed = 95.0
            self._ready = False
            self._passthrough = None
            self._menu_open = False
            self._quit_after_hide = False
            self._act = Activity()
            self._last_status: tuple = ()
            self._t_last = time.monotonic()

            self._timers = []
            for ms, fn in ((8, self._physics), (33, self._hit_tick), (100, self._brain_tick), (1500, self._sample_activity), (50, self._poll_stdin)):
                t = QtCore.QTimer(self)
                t.timeout.connect(fn)
                t.start(ms)
                self._timers.append(t)
            self._stdin_q: "queue.Queue[Optional[dict]]" = queue.Queue()
            if not standalone:
                threading.Thread(target=self._stdin_reader, name="pet-stdin", daemon=True).start()
            self._setup_tray()

        # ------------------------------------------------------------ размер / геометрия
        def _apply_size(self):
            self.setFixedSize(int(BASE_W * self.scale), int(BASE_H * self.scale))

        def resizeEvent(self, e):                                                 # noqa: N802
            super().resizeEvent(e)
            if hasattr(self, "view"):
                self.view.setGeometry(0, 0, self.width(), self.height())

        def _cx(self) -> float:
            return float(self.lay["center_x"])

        def _feet(self) -> float:
            return float(self.lay["feet_y"])

        def _screen_geo(self, gx: Optional[float] = None):
            x = self.fx + self._cx() if gx is None else gx
            pt = QtCore.QPoint(int(x), int(self.fy + self._feet() - 4))
            scr = QtGui.QGuiApplication.screenAt(pt) or QtGui.QGuiApplication.primaryScreen()
            return scr.availableGeometry()

        def _floor_top(self, geo) -> float:
            return geo.bottom() + 1 - self._feet() - FLOOR_PAD

        def _move_to(self, x: float, y: float):
            self.fx, self.fy = x, y
            self.move(int(round(x)), int(round(y)))

        # ------------------------------------------------------------ связь с рендерером
        def _js(self, msg: dict):
            self.page.runJavaScript("window.PetApi&&PetApi.recv(" + json.dumps(msg, ensure_ascii=False) + ")")

        def _on_render_died(self, status, code):
            emit_event(ev="info", msg=f"render process завершился ({code}); перезагрузка")
            QtCore.QTimer.singleShot(500, self.view.reload)

        def _on_js(self, ev: dict):
            e = ev.get("ev")
            now = time.monotonic()
            if e == "layout":
                self._on_layout(ev)
            elif e == "rects":
                self.rects = ev.get("rects", [])
            elif e == "ready":
                self._ready = True
                clips = ev.get("clips", {})
                emit_event(ev="ready", clips=clips, portal=ev.get("portal"), renderer=ev.get("renderer"), binding=Q.name)
                self._spawn()
            elif e == "grab":
                if self._left_down() and self.brain.state not in ("hidden", "dissolving", "materializing"):
                    self._pending_grab = (now, QtGui.QCursor.pos())
            elif e == "release":
                if self._drag:
                    self._end_drag()
                self._pending_grab = None
            elif e == "context":
                self._show_menu()
            elif e in ("clip_end", "portal_done", "dissolve_done", "dblclick"):
                self._apply(self.brain.on_event(ev, now))
                if e == "dissolve_done" and ev.get("dir") == "out" and self._quit_after_hide:
                    QtWidgets.QApplication.quit()
            elif e == "error":
                emit_event(ev="error", msg="рендерер: " + str(ev.get("msg", ""))[:400])
            elif e == "warn":
                sys.stderr.write("[pet] " + str(ev.get("msg", "")) + "\n")

        def _on_layout(self, ev: dict):
            self.lay = {"feet_y": float(ev["feet_y"]), "center_x": float(ev["center_x"]), "ppm": float(ev.get("ppm", 300))}
            if self.isVisible() and not self._drag and not self._fall and self._anchor_cx is not None:
                geo = self._screen_geo(self._anchor_cx)
                self._move_to(self._anchor_cx - self._cx(), self._floor_top(geo))

        # ------------------------------------------------------------ применение команд мозга
        def _apply(self, cmds: list):
            for c in cmds:
                t = c.get("type")
                if t == "play":
                    m = {"cmd": "play", "clip": c["clip"]}
                    for k in ("loop", "fade", "speed"):
                        if k in c:
                            m[k] = c[k]
                    self._js(m)
                elif t == "face":
                    self._js({"cmd": "face", "deg": c["deg"], "fast": bool(c.get("fast", False))})
                elif t == "walk_to":
                    self._walk_goal = float(c["x"])
                    self._walk_speed = float(c.get("speed", 95)) * (self.scale / 0.8)
                elif t == "stop_walk":
                    self._walk_goal = None
                elif t == "portal":
                    self._js({"cmd": "portal", "action": c.get("action", "run"), **({"item": c["item"]} if "item" in c else {})})
                elif t == "dissolve":
                    self._js({"cmd": "dissolve", "dir": c["dir"]})
                elif t == "fps":
                    self._js({"cmd": "fps", "value": c["value"]})
                elif t == "show_window":
                    self._spawn_window()
                elif t == "hide_window":
                    self.hide()
                    self._passthrough = None
                    if self._quit_after_hide:
                        QtWidgets.QApplication.quit()
            self._emit_status()

        def _emit_status(self):
            cur = (self.brain.state, self._act.kind, self.brain.mode)
            if cur != self._last_status:
                self._last_status = cur
                emit_event(ev="status", state=cur[0], activity=cur[1], mode=cur[2])

        # ------------------------------------------------------------ появление
        def _spawn(self):
            self._apply(self.brain.show(time.monotonic()))

        def _spawn_window(self):
            pg = QtGui.QGuiApplication.primaryScreen().availableGeometry()
            cx = pg.left() + pg.width() * 0.72
            geo = self._screen_geo(cx)
            self._anchor_cx = cx
            self._move_to(cx - self._cx(), self._floor_top(geo))
            self._fall = self._drag = False
            self.show()
            self._set_passthrough(True)

        # ------------------------------------------------------------ мышь, перетаскивание, физика
        @staticmethod
        def _left_down() -> bool:
            return bool(QtGui.QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)

        def _begin_drag(self):
            cur = QtGui.QCursor.pos()
            self._drag, self._fall, self._walk_goal = True, False, None
            self._grab_dx, self._grab_dy = cur.x() - self.x(), cur.y() - self.y()
            self._trail = [(time.monotonic(), float(cur.x()), float(cur.y()))]
            self._apply(self.brain.on_event({"ev": "grab"}, time.monotonic()))

        def _end_drag(self):
            if not self._drag:
                return
            self._drag = False
            vx = vy = 0.0
            tr = [p for p in self._trail if p[0] >= time.monotonic() - 0.12]
            if len(tr) >= 2 and tr[-1][0] > tr[0][0]:
                dt = tr[-1][0] - tr[0][0]
                vx, vy = (tr[-1][1] - tr[0][1]) / dt, (tr[-1][2] - tr[0][2]) / dt
            lim = 2200.0 * self.scale
            self._vx, self._vy = max(-lim, min(lim, vx)), max(-lim, min(lim, vy)) * 0.6
            self._fall, self._bounced = True, False
            self._apply(self.brain.on_event({"ev": "release"}, time.monotonic()))

        def _physics(self):
            now = time.monotonic()
            dt = max(0.001, min(0.05, now - self._t_last))
            self._t_last = now
            if not self.isVisible():
                return
            if self._pending_grab and not self._drag:
                t0, p0 = self._pending_grab
                cur = QtGui.QCursor.pos()
                if not self._left_down():
                    self._pending_grab = None                       # обычный клик без перетаскивания
                elif math.hypot(cur.x() - p0.x(), cur.y() - p0.y()) > DRAG_START_PX or now - t0 > DRAG_START_S:
                    self._pending_grab = None
                    self._begin_drag()
            if self._drag:
                if not self._left_down():
                    self._end_drag()
                    return
                cur = QtGui.QCursor.pos()
                self._move_to(cur.x() - self._grab_dx, cur.y() - self._grab_dy)
                self._trail.append((now, float(cur.x()), float(cur.y())))
                del self._trail[:-12]
                return
            geo = self._screen_geo()
            floor_top = self._floor_top(geo)
            if self._fall:
                g = GRAVITY * max(0.6, self.scale)
                self._vy += g * dt
                nx, ny = self.fx + self._vx * dt, self.fy + self._vy * dt
                lo, hi = geo.left() + 0.28 * self.lay["ppm"], geo.right() - 0.28 * self.lay["ppm"]
                cx = nx + self._cx()
                if cx < lo:
                    nx, self._vx = lo - self._cx(), abs(self._vx) * 0.4
                elif cx > hi:
                    nx, self._vx = hi - self._cx(), -abs(self._vx) * 0.4
                if ny >= floor_top:
                    ny = floor_top
                    if self._vy > 520 * self.scale and not self._bounced:
                        self._vy, self._bounced, self._vx = -self._vy * 0.22, True, self._vx * 0.6
                    else:
                        self._fall, self._vx, self._vy = False, 0.0, 0.0
                        self._move_to(nx, ny)
                        self._anchor_cx = nx + self._cx()
                        self._apply(self.brain.on_event({"ev": "landed"}, now))
                        return
                self._move_to(nx, ny)
                return
            if self._walk_goal is not None:
                cx = self.fx + self._cx()
                dx = self._walk_goal - cx
                step = self._walk_speed * dt
                if abs(dx) <= step:
                    self._move_to(self._walk_goal - self._cx(), floor_top)
                    self._walk_goal = None
                    self._apply(self.brain.on_event({"ev": "walk_done"}, now))
                else:
                    self._move_to(self.fx + math.copysign(step, dx), floor_top)
                self._anchor_cx = self.fx + self._cx()
                return
            # на месте: подстраиваемся под панель задач/смену разрешения
            if abs(self.fy - floor_top) > 2 and self.brain.state not in ("hidden", "materializing"):
                self._fall, self._vx, self._vy, self._bounced = True, 0.0, 0.0, True
            self._anchor_cx = self.fx + self._cx()

        # ------------------------------------------------------------ «сквозное» окно: клики мимо персонажа уходят в рабочий стол
        def _set_passthrough(self, on: bool):
            if on == self._passthrough:
                return
            self._passthrough = on
            try:
                if sys.platform == "win32":
                    import ctypes
                    user32 = ctypes.windll.user32
                    get = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
                    setw = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
                    get.restype = ctypes.c_ssize_t
                    get.argtypes = [ctypes.c_void_p, ctypes.c_int]
                    setw.restype = ctypes.c_ssize_t
                    setw.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
                    hwnd = int(self.winId())
                    ex = get(hwnd, -20)                               # GWL_EXSTYLE
                    ex = (ex | 0x20) if on else (ex & ~0x20)           # WS_EX_TRANSPARENT
                    setw(hwnd, -20, ex)
                else:
                    wh = self.windowHandle()
                    if wh is not None:
                        wh.setFlag(Qt.WindowType.WindowTransparentForInput, on)
            except Exception:                                         # noqa: BLE001
                pass

        def _hit_tick(self):
            if not self.isVisible():
                return
            if self._drag or self._pending_grab or self._menu_open:
                self._set_passthrough(False)
                return
            p = QtGui.QCursor.pos()
            lx, ly = p.x() - self.x(), p.y() - self.y()
            inside = any(r[0] <= lx <= r[0] + r[2] and r[1] <= ly <= r[1] + r[3] for r in self.rects)
            self._set_passthrough(not inside)

        # ------------------------------------------------------------ такты
        def _sample_activity(self):
            self._act = self.tracker.sample()

        def _brain_tick(self):
            if not self._ready:
                return
            geo = self._screen_geo()
            half = 0.30 * self.lay["ppm"]
            ctx = {"activity": self._act.kind, "user_idle": self._act.user_idle, "x": self.fx + self._cx(),
                   "xmin": geo.left() + half, "xmax": geo.right() - half,
                   "park_x": geo.left() + geo.width() * float(self.cfg.get("park_x", 0.82)), "portal": self.has_portal}
            self._apply(self.brain.tick(time.monotonic(), ctx))

        # ------------------------------------------------------------ команды от терминала
        def _stdin_reader(self):
            try:
                for line in sys.stdin:
                    line = line.strip()
                    if line:
                        try:
                            self._stdin_q.put(json.loads(line))
                        except ValueError:
                            pass
            finally:
                self._stdin_q.put(None)                               # родитель закрыл канал

        def _poll_stdin(self):
            while True:
                try:
                    m = self._stdin_q.get_nowait()
                except queue.Empty:
                    return
                if m is None:
                    self._quit_now()
                    return
                self._on_command(m)

        def _on_command(self, m: dict):
            c, now = m.get("cmd"), time.monotonic()
            if c == "mode":
                self.cfg["mode"] = m.get("value", "auto")
                self._apply(self.brain.set_mode(self.cfg["mode"], now))
            elif c == "play":
                self._apply(self.brain.play_clip(str(m.get("clip", "idle")), now))
            elif c == "portal":
                self._apply(self.brain.portal_now(now))
            elif c == "hide":
                self._apply(self.brain.hide(now))
            elif c == "show":
                self._apply(self.brain.show(now))
            elif c == "scale":
                self.scale = max(0.4, min(2.5, float(m.get("value", 0.8))))
                self.cfg["scale"] = self.scale
                self._apply_size()
            elif c == "quit":
                self._quit_after_hide = True
                if self.isVisible() and self.brain.state not in ("hidden",):
                    self._apply(self.brain.hide(now))
                    QtCore.QTimer.singleShot(3500, QtWidgets.QApplication.quit)
                else:
                    QtWidgets.QApplication.quit()
            elif c == "ping":
                emit_event(ev="pong")

        def _quit_now(self):
            if self.standalone:
                return
            QtWidgets.QApplication.quit()

        # ------------------------------------------------------------ меню и трей
        def _build_menu(self, parent=None):
            m = QtWidgets.QMenu(parent)
            a = m.addAction("Достать из портала")
            a.setEnabled(self.has_portal)
            a.triggered.connect(lambda *_: self._apply(self.brain.portal_now(time.monotonic())))
            sub = m.addMenu("Режим")
            grp = QtGui.QActionGroup(sub)
            for key, label in (("auto", "Авто (реагирует на окна)"), ("free", "Свободный"), ("still", "Спокойный")):
                act = sub.addAction(label)
                act.setCheckable(True)
                act.setChecked(self.brain.mode == key)
                grp.addAction(act)
                act.triggered.connect(lambda *_, k=key: self._on_command({"cmd": "mode", "value": k}))
            m.addSeparator()
            if self.brain.state == "hidden":
                m.addAction("Показать").triggered.connect(lambda *_: self._apply(self.brain.show(time.monotonic())))
            else:
                m.addAction("Спрятать (испарить)").triggered.connect(lambda *_: self._apply(self.brain.hide(time.monotonic())))
            m.addAction("Выключить 3D-агента").triggered.connect(lambda *_: self._on_command({"cmd": "quit"}))
            return m

        def _show_menu(self):
            self._menu_open = True
            self._set_passthrough(False)
            try:
                self._build_menu(self).exec(QtGui.QCursor.pos())
            finally:
                self._menu_open = False

        def _setup_tray(self):
            self.tray = None
            if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
                return
            pm = QtGui.QPixmap(32, 32)
            pm.fill(QtGui.QColor(0, 0, 0, 0))
            p = QtGui.QPainter(pm)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setBrush(QtGui.QColor(34, 28, 66))
            p.setPen(QtGui.QPen(QtGui.QColor(53, 242, 255), 2))
            p.drawEllipse(3, 3, 26, 26)
            p.setBrush(QtGui.QColor(53, 242, 255))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(10, 13, 4, 4)
            p.drawEllipse(18, 13, 4, 4)
            p.end()
            self.tray = QtWidgets.QSystemTrayIcon(QtGui.QIcon(pm), self)
            self.tray.setToolTip("Alontito 3D Pet")
            self.tray.activated.connect(lambda reason: self._tray_click(reason))
            self.tray.show()

        def _tray_click(self, reason):
            R = QtWidgets.QSystemTrayIcon.ActivationReason
            if reason == R.Trigger:
                now = time.monotonic()
                self._apply(self.brain.show(now) if self.brain.state == "hidden" else self.brain.hide(now))
            elif reason == R.Context and self.tray is not None:
                self.tray.setContextMenu(self._build_menu())

    return PetWindow


# ====================================================================================== запуск дочернего процесса
def run_child(standalone: bool = False, debug: bool = False) -> int:
    cfg = load_config()
    if not standalone:
        cfg["mode"] = cfg.get("mode", "auto")
    try:
        Q = _load_qt()
    except RuntimeError as e:
        emit_event(ev="error", msg=str(e))
        print(str(e), file=sys.stderr)
        return 2
    for need in ("index.html", "pet.js", "animations.json"):
        if not (WEB_DIR / need).is_file():
            msg = f"нет файла {WEB_DIR / need}"
            if need == "animations.json":
                msg += " — выполни: python tools/build_pet_clips.py"
            emit_event(ev="error", msg=msg)
            return 3
    char, portal = find_asset("character", cfg), find_asset("portal", cfg)
    if not char:
        emit_event(ev="error", msg=f"не найден .glb персонажа — положи hooded_neon_character_rigged.glb в {ASSETS_DIR}")
        return 3
    local = ensure_vendor(log=lambda s: emit_event(ev="info", msg=s))
    if not local:
        emit_event(ev="info", msg="three.js не скачан — используется CDN (нужен интернет)")
    server = PetServer(WEB_DIR, {"character": char, "portal": portal}, use_vendor=local)
    port = server.start()

    flags = ["--ignore-gpu-blocklist", "--enable-gpu-rasterization"]
    if cfg.get("gpu") == "software":
        flags += ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "") + " " + " ".join(flags)).strip()
    try:
        Q.QtCore.QCoreApplication.setAttribute(Q.QtCore.Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    except Exception:                                                 # noqa: BLE001 — в новых Qt атрибут не нужен
        pass
    app = Q.QtWidgets.QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Alontito 3D Pet")

    Window = _build_window_class(Q)
    brain = PetBrain(cfg, now=time.monotonic())
    win = Window(cfg, port, bool(portal), ActivityTracker(cfg), brain, debug=debug, standalone=standalone)
    emit_event(ev="info", msg=f"{Q.name}: окно создано, жду рендерер")
    code = app.exec()
    server.stop()
    return int(code)


def run_check() -> int:
    cfg = load_config()
    ok = True
    print(f"Python {sys.version.split()[0]} / {sys.platform}")
    try:
        Q = _load_qt()
        print(f"[ок]  Qt: {Q.name}")
    except RuntimeError as e:
        print(f"[!!]  {e}")
        ok = False
    for kind in ("character", "portal"):
        f = find_asset(kind, cfg)
        print(f"[{'ок' if f else ('!!' if kind == 'character' else '--')}]  {kind}: {f or 'не найден (положи .glb в ' + str(ASSETS_DIR) + ')'}")
        ok = ok and (f is not None or kind == "portal")
    print(f"[{'ок' if vendor_ready() else '--'}]  three.js локально: {'да' if vendor_ready() else 'нет (скачается при первом запуске или возьмётся с CDN)'}")
    for need in ("index.html", "pet.js", "animations.json"):
        e = (WEB_DIR / need).is_file()
        print(f"[{'ок' if e else '!!'}]  pet3d/{need}")
        ok = ok and e
    act = ActivityTracker(cfg).sample()
    print(f"[ок]  активное окно: {act.kind} exe={act.exe!r} title={act.title[:40]!r} простой={act.user_idle:.0f}c" if sys.platform == "win32"
          else "[--]  трекер активности работает только на Windows (Win32); задай ALONTITO_PET_FAKE_ACTIVITY=coding для проверки")
    print(f"      конфиг: {CONFIG_PATH if CONFIG_PATH.is_file() else (FALLBACK_CONFIG_PATH if FALLBACK_CONFIG_PATH.is_file() else 'по умолчанию')}")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Alontito 3D Desktop Pet")
    ap.add_argument("--child", action="store_true", help="служебный режим: управляется терминалом по stdin/stdout")
    ap.add_argument("--standalone", action="store_true", help="запустить питомца без терминала")
    ap.add_argument("--check", action="store_true", help="диагностика окружения")
    ap.add_argument("--download-vendor", action="store_true", help="скачать three.js и выйти")
    ap.add_argument("--debug", action="store_true", help="печатать console.log рендерера в stderr")
    a = ap.parse_args(argv)
    if a.check:
        return run_check()
    if a.download_vendor:
        print("three.js готов" if ensure_vendor(print) else "не удалось скачать three.js")
        return 0
    if a.child or a.standalone:
        return run_child(standalone=a.standalone, debug=a.debug)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
