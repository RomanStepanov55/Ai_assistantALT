#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alontito App Server — веб-приложение личного агента (телефон + планшет + ПК).

Запуск из корня репозитория:
    python pult/server.py            # или двойной клик по Pult.bat

Открыть на этом ПК:      http://127.0.0.1:8787/?token=<TOKEN>
С телефона (одна Wi-Fi):  http://<IP-ПК>:8787  → код сопряжения с экрана ПК

Сопряжение: на ПК (вкладка «Настройки») генерируется 6-значный код на 5
минут; новое устройство вводит код и получает собственный токен. Отозвать
устройство можно в любой момент — код сопряжения не даёт доступа к данным,
пока не подтверждён на ПК.

HARD RULE #2 (2026-11-24) и разворот 2026-11-27: агент не играет и игры в
проекте больше нет. Здесь нет ни кадра экрана, ни телеметрии силы, ни
эндпоинтов действий в игре. Всё, что делает сервер: чат с агентом (свой
мозг), память фактов, напоминания, входящие с устройств и контур обучения.

Зависимостей нет: http.server + SSE, urllib, sqlite3.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import secrets
import socket
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))          # make `alontito.*` importable
STATIC = HERE / "static"
DATA = ROOT / "data"
PET_WEB = ROOT / "alontito" / "pet3d"
PET_ASSETS = ROOT / "alontito" / "assets" / "3d"
CONFIG_FILE = Path(os.environ.get("ALONTITO_PULT_CONFIG") or (HERE / "pult_config.json"))
DEVICES_FILE = DATA / "alontito" / "devices.json"
CHAT_DB = DATA / "pult_chat.sqlite3"
JOURNAL_FILE = DATA / "alontito" / "journal.jsonl"
MAX_BODY = 25 * 1024 * 1024             # 25 MB: voice notes and photos
MAX_MESSAGE = 8000
MAX_LOG_TAIL = 256 * 1024
PAIR_CODE_TTL = 300                     # 5 minutes
PAIR_MAX_ATTEMPTS = 5

_MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".glb": "model/gltf-binary", ".png": "image/png", ".jpg": "image/jpeg",
    ".webmanifest": "application/manifest+json", ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".wav": "audio/wav",
    ".webp": "image/webp", ".gif": "image/gif", ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".webm": "video/webm",
}

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def load_config() -> dict:
    """Приложение живёт в домашней сети: по умолчанию слушает 0.0.0.0.

    Кадра экрана здесь больше нет, поэтому открытый порт не раскрывает
    рабочий стол; данные закрыты мастер-токеном и токенами устройств.
    Вернуть всё на этот ПК можно: "host": "127.0.0.1" в pult_config.json.
    """
    cfg = {
        "host": "0.0.0.0",
        "port": 8787,
        "token": None,
        "history_limit": 12,
    }
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[PULT] config unreadable, using defaults: {e}")
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(16)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return cfg


CFG = load_config()
TOKEN = CFG["token"]

# --------------------------------------------------------------------------
# Devices and pairing
# --------------------------------------------------------------------------

class Devices:
    """Устройства, сопряжённые с агентом. Мастер-токен у ПК."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.items: list = []
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = raw.get("devices", []) if isinstance(raw, dict) else []
        except (OSError, ValueError):
            self.items = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"devices": self.items}, ensure_ascii=False,
                                  indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def knows(self, token: str) -> bool:
        with self._lock:
            return any(d.get("token") == token and d.get("enabled", True)
                       for d in self.items)

    def touch(self, token: str) -> None:
        with self._lock:
            for d in self.items:
                if d.get("token") == token:
                    d["last_seen"] = time.time()
                    self._save()
                    return

    def add(self, name: str) -> dict:
        with self._lock:
            device = {
                "id": secrets.token_hex(4),
                "name": (name or "устройство").strip()[:40] or "устройство",
                "token": secrets.token_urlsafe(16),
                "created": time.time(),
                "last_seen": None,
                "enabled": True,
            }
            self.items.append(device)
            self._save()
            return device

    def set_enabled(self, device_id: str, enabled: bool) -> bool:
        with self._lock:
            for d in self.items:
                if d.get("id") == device_id:
                    d["enabled"] = enabled
                    self._save()
                    return True
        return False

    def public(self) -> list:
        return [{k: d.get(k) for k in ("id", "name", "created", "last_seen",
                                       "enabled")}
                for d in self.items]


class Pairing:
    """Коды сопряжения: 6 цифр, 5 минут, одноразовые. Подбор кода ограничивается
    глобальным лимитом: не больше 10 попыток ввода кода в минуту."""

    RATE_LIMIT = 10

    def __init__(self):
        self._lock = threading.Lock()
        self._codes: dict = {}
        self._verify_times: list = []

    def new_code(self) -> dict:
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self._lock:
            now = time.time()
            for c in [k for k, v in self._codes.items()
                      if v["expires"] < now]:
                del self._codes[c]
            self._codes[code] = {"expires": now + PAIR_CODE_TTL,
                                 "used": False}
        return {"code": code, "expires_in": PAIR_CODE_TTL}

    def verify(self, code: str) -> dict:
        """Returns {'ok': True} or {'ok': False, 'reason': ...}."""
        with self._lock:
            now = time.time()
            self._verify_times = [t for t in self._verify_times
                                  if now - t < 60]
            if len(self._verify_times) >= self.RATE_LIMIT:
                return {"ok": False,
                        "reason": "слишком много попыток подбора — "
                                  "подождите минуту"}
            self._verify_times.append(now)
            entry = self._codes.get((code or "").strip())
            if entry is None:
                return {"ok": False, "reason": "код не найден или истёк"}
            if entry["used"]:
                return {"ok": False, "reason": "код уже использован"}
            if entry["expires"] < now:
                del self._codes[(code or "").strip()]
                return {"ok": False,
                        "reason": "код истёк — сгенерируйте новый на ПК"}
            entry["used"] = True
        return {"ok": True}


DEVICES = Devices(DEVICES_FILE)
PAIRING = Pairing()

# --------------------------------------------------------------------------
# The agent (lazy-safe: the app boots even while libraries are missing)
# --------------------------------------------------------------------------

class AgentBinding:
    def __init__(self):
        self.agent = None
        self._lock = threading.Lock()

    def bind(self):
        with self._lock:
            if self.agent is not None:
                return self.agent
            try:
                from alontito.agent import Agent
                self.agent = Agent()
            except Exception as e:
                print(f"[PULT] agent unavailable: {e}")
                self.agent = None
            return self.agent

    def ask(self, text: str, device: str, history: list,
            on_delta=None) -> dict:
        agent = self.bind()
        if agent is None:
            from alontito.model import offline_reply
            return {"text": offline_reply(text, {"own": {"enabled": False}}),
                    "source": "offline"}
        return agent.answer(text, device=device, history=history,
                            on_delta=on_delta)

    def bound(self):
        agent = self.bind()
        return agent if agent is not None else self


AGENT = AgentBinding()

# --------------------------------------------------------------------------
# Chat history (sqlite, same store the old pult used)
# --------------------------------------------------------------------------

def db_conn() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CHAT_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS chat(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL, role TEXT, text TEXT)""")
    return conn


def chat_history(limit: int = 50) -> list:
    try:
        conn = db_conn()
        rows = conn.execute(
            "SELECT role, text FROM chat ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        conn.close()
        return [{"role": r, "text": t} for r, t in reversed(rows)]
    except Exception:
        return []


def chat_save(role: str, text: str):
    try:
        conn = db_conn()
        conn.execute("INSERT INTO chat(ts, role, text) VALUES(?,?,?)",
                     (time.time(), role, text))
        conn.commit()
        conn.close()
    except Exception:
        pass


# --------------------------------------------------------------------------
# SSE broker
# --------------------------------------------------------------------------

class Broker:
    def __init__(self):
        self.subs: list = []
        self._lock = threading.Lock()
        self._offsets: dict = {}

    def subscribe(self):
        q = queue.Queue(maxsize=400)
        with self._lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self.subs:
                self.subs.remove(q)

    def publish(self, event: dict):
        with self._lock:
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    def snapshot_lines(self) -> list:
        return self._tail(first=True)

    def tail_logs(self) -> list:
        return self._tail(first=False)

    def _tail(self, first: bool) -> list:
        events = []
        try:
            if not JOURNAL_FILE.exists():
                return events
            size = JOURNAL_FILE.stat().st_size
            last = 0 if first else self._offsets.get("journal", 0)
            if size < last:
                last = 0
            if size == last:
                return events
            with JOURNAL_FILE.open("rb") as f:
                f.seek(min(last, max(0, size - MAX_LOG_TAIL)))
                tail = f.read().decode("utf-8", errors="replace")
            lines = tail.splitlines()
            for line in (lines if first else lines[-30:]):
                line = line.strip()
                if line:
                    events.append({"type": "log", "line": line[-400:]})
            self._offsets["journal"] = size
        except Exception:
            pass
        return events


BROKER = Broker()


# --------------------------------------------------------------------------
# Voice transcription (optional, fully offline via Vosk)
# --------------------------------------------------------------------------

def transcribe_wav(data: bytes) -> dict:
    """16 kHz mono WAV → text, если установлен Vosk. Честный отказ иначе."""
    try:
        from vosk import Model, KaldiRecognizer          # optional
        import wave
        import io as _io
    except ImportError:
        return {"transcript": None,
                "detail": "Vosk не установлен — запись сохранена без расшифровки "
                          "(pip install vosk, затем модель https://alphacephei.com/vosk)"}
    try:
        wf = wave.open(_io.BytesIO(data))
        if wf.getframerate() != 16000 or wf.getnchannels() != 1:
            return {"transcript": None,
                    "detail": "нужен WAV 16 кГц моно; запись сохранена как есть"}
        model = Model(str(ROOT / "data" / "vosk-model"))
        rec = KaldiRecognizer(model, 16000)
        text = ""
        while True:
            chunk = wf.readframes(4000)
            if not chunk:
                break
            if rec.AcceptWaveform(chunk):
                text += (json.loads(rec.Result()).get("text") or "") + " "
        text += json.loads(rec.FinalResult()).get("text") or ""
        return {"transcript": text.strip() or None, "detail": ""}
    except Exception as exc:
        return {"transcript": None, "detail": f"расшифровка не удалась: {exc}"}


# --------------------------------------------------------------------------
# Background tasks: reminders fire on every device
# --------------------------------------------------------------------------

def background_tasks():
    from alontito.automations import Automations
    autos = Automations()
    while True:
        try:
            now = time.time()
            for auto in autos.due(now):
                BROKER.publish({"type": "notification", "kind": "reminder",
                                "text": auto.text,
                                "when": auto.when_text(),
                                "automation_id": auto.id})
                autos.mark_fired(auto.id, now)
            BROKER.publish({"type": "status", "status": state_snapshot()})
            for ev in BROKER.tail_logs():
                BROKER.publish(ev)
        except Exception as e:
            print(f"[PULT] background error: {e}")
        time.sleep(5.0)


def state_snapshot() -> dict:
    agent = AGENT.bind()
    st = agent.status() if agent is not None else {}
    return {
        "time": time.strftime("%H:%M:%S"),
        "agent": st,
        "devices": DEVICES.public(),
        "unprocessed": len(_inbox_safe().list(state="new")),
    }


def _inbox_safe():
    from alontito.inbox import Inbox
    return Inbox()


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

class BodyTooLarge(Exception):
    pass


class PultHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AlontitoApp/2.0"

    def log_message(self, fmt, *args):   # quiet: токен не должен попасть в лог
        pass

    # ---- auth ---------------------------------------------------------

    def _token_from_request(self) -> str:
        header = self.headers.get("X-Pult-Token", "")
        query = urllib.parse.urlparse("http://x" + self.path).query
        from urllib.parse import parse_qs
        qtoken = parse_qs(query).get("token", [None])[0]
        return (header or qtoken or "")

    def _is_master(self, token: str) -> bool:
        return secrets.compare_digest(token.encode("utf-8"),
                                      TOKEN.encode("utf-8"))

    def _authorized(self) -> bool:
        if _is_public(self.path.split("?")[0]):
            return True
        token = self._token_from_request()
        if not token:
            return False
        if self._is_master(token):
            return True
        if DEVICES.knows(token):
            DEVICES.touch(token)
            return True
        return False

    # ---- helpers -------------------------------------------------------

    def _safe_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")   # pet screen is an iframe
        self.send_header("Referrer-Policy", "no-referrer")

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._safe_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, ctype: str, status: int = 200,
                    cache: str = "no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self._safe_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length > MAX_BODY:
            # Drain the socket first: a 413 written over an unread stream
            # aborts the connection on the client instead of answering it.
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise BodyTooLarge()
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> dict:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _need_master(self) -> bool:
        return not self._is_master(self._token_from_request())

    # ---- GET -----------------------------------------------------------

    def do_GET(self):
        try:
            if not self._authorized():
                return self._send_json({"error": "unauthorized"}, 401)
            url = urllib.parse.urlparse("http://x" + self.path)
            path = url.path
            from urllib.parse import parse_qs
            qs = parse_qs(url.query)

            # --- static shell (public names, whitelisted) --------------
            if path in ("/", "/index.html", "/app.js", "/style.css",
                        "/manifest.webmanifest", "/sw.js"):
                return self._send_static(path.lstrip("/") or "index.html")
            if path.startswith("/icons/"):
                return self._send_static(path.lstrip("/"))

            # --- 3D pet (device-agnostic screen) ------------------------
            if path == "/pet":
                return self._send_pet_index()
            if path in ("/pet/pet.js", "/pet/animations.json"):
                return self._send_pet_file(path.split("/")[-1])
            if path.startswith("/pet/vendor/"):
                return self._send_pet_vendor(path[len("/pet/vendor/"):])
            if path == "/pet/asset/character.glb":
                return self._send_pet_character()

            # --- state / journal ----------------------------------------
            if path == "/api/state":
                return self._send_json(state_snapshot())
            if path == "/api/journal":
                try:
                    n = int(qs.get("n", [100])[0])
                except ValueError:
                    n = 100
                return self._send_json({"lines": journal_tail(max(1, min(n, 1000)))})
            if path == "/api/events":
                return self._sse()

            # --- chat ----------------------------------------------------
            if path == "/api/chat/history":
                return self._send_json({"messages": chat_history(50)})

            # --- automations ---------------------------------------------
            if path == "/api/automations":
                return self._send_json({"items": [a.row() for a in AGENT.bind().automations.list()]})

            # --- inbox ---------------------------------------------------
            if path == "/api/inbox":
                try:
                    limit = int(qs.get("limit", [100])[0])
                except ValueError:
                    limit = 100
                state = (qs.get("state", [None])[0] or None)
                return self._send_json({"items": _inbox_safe().list(state=state, limit=limit)})
            m = re_match(path, r"^/api/inbox/([\w-]+)$")
            if m:
                item = _inbox_safe().get(m.group(1))
                return self._send_json({"item": item}, 404 if item is None else 200)
            m = re_match(path, r"^/api/inbox/([\w-]+)/file$")
            if m:
                return self._send_inbox_file(m.group(1))
            m = re_match(path, r"^/api/inbox/([\w-]+)/transcript$")
            if m:
                return self._transcribe_inbox(m.group(1))

            # --- training loop -------------------------------------------
            if path == "/api/training":
                return self._send_json({"pending": AGENT.bind().pending_examples(100),
                                        "stats": AGENT.bind()._learning().stats()})
            if path == "/api/facts":
                facts = AGENT.bind().memory.facts()
                return self._send_json({"facts": facts[-100:]})

            # --- devices (master only) ------------------------------------
            if path == "/api/devices":
                if self._need_master():
                    return self._send_json({"error": "только для ПК"}, 403)
                return self._send_json({"devices": DEVICES.public()})

            return self._send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:                              # noqa: BLE001
            return self._send_json({"error": f"server error: {e}"}, 500)

    # ---- POST / DELETE ---------------------------------------------------

    def do_POST(self):
        try:
            if not self._authorized():
                return self._send_json({"error": "unauthorized"}, 401)
            url = urllib.parse.urlparse("http://x" + self.path)
            path = url.path
            try:
                body = self._read_body()
            except BodyTooLarge:
                return self._send_json({"error": "body too large"}, 413)

            if path == "/api/chat":
                return self._handle_chat(body)

            # pairing
            if path == "/api/pairing-code":
                if self._need_master():
                    return self._send_json({"error": "код генерирует только ПК"}, 403)
                return self._send_json(PAIRING.new_code())
            if path == "/api/pair":
                return self._handle_pair(body)

            # automations
            if path == "/api/automations":
                data = json.loads(body.decode("utf-8") or "{}")
                return self._add_automation(str(data.get("text") or ""))
            m = re_match(path, r"^/api/automations/([\w-]+)/run$")
            if m:
                return self._run_automation(m.group(1))

            # inbox
            if path == "/api/inbox":
                return self._add_inbox(body)
            m = re_match(path, r"^/api/inbox/([\w-]+)/state$")
            if m:
                data = json.loads(body.decode("utf-8") or "{}")
                ok_flag = _inbox_safe().set_state(
                    m.group(1), str(data.get("state") or ""),
                    transcript=data.get("transcript"))
                return self._send_json({"ok": ok_flag},
                                       400 if not ok_flag else 200)
            m = re_match(path, r"^/api/inbox/([\w-]+)/process$")
            if m:
                return self._process_inbox(m.group(1))

            # training
            if path == "/api/training/approve":
                data = json.loads(body.decode("utf-8") or "{}")
                ok_flag = AGENT.bind().approve_example(str(data.get("fingerprint") or ""))
                return self._send_json({"ok": ok_flag}, 404 if not ok_flag else 200)
            if path == "/api/training/dismiss":
                data = json.loads(body.decode("utf-8") or "{}")
                ok_flag = AGENT.bind().dismiss_example(str(data.get("fingerprint") or ""))
                return self._send_json({"ok": ok_flag}, 404 if not ok_flag else 200)
            if path == "/api/training/export":
                result = AGENT.bind()._learning().export_approved()
                return self._send_json(result)

            # facts
            if path == "/api/facts":
                data = json.loads(body.decode("utf-8") or "{}")
                return self._add_fact(data)

            # devices (master only)
            m = re_match(path, r"^/api/devices/([\w-]+)/(revoke|enable)$")
            if m:
                if self._need_master():
                    return self._send_json({"error": "только для ПК"}, 403)
                ok_flag = DEVICES.set_enabled(m.group(1), m.group(2) == "enable")
                return self._send_json({"ok": ok_flag}, 404 if not ok_flag else 200)

            return self._send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:                              # noqa: BLE001
            return self._send_json({"error": f"server error: {e}"}, 500)

    def do_DELETE(self):
        try:
            if not self._authorized():
                return self._send_json({"error": "unauthorized"}, 401)
            path = urllib.parse.urlparse("http://x" + self.path).path
            m = re_match(path, r"^/api/automations/([\w-]+)$")
            if m:
                ok_flag = AGENT.bind().automations.remove(m.group(1))
                return self._send_json({"ok": ok_flag},
                                       404 if not ok_flag else 200)
            return self._send_json({"error": "not found"}, 404)
        except Exception as e:                              # noqa: BLE001
            return self._send_json({"error": f"server error: {e}"}, 500)

    # ---- feature handlers -------------------------------------------------

    def _handle_chat(self, body: bytes) -> None:
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            data = {}
        msg = (data.get("message") or "").strip()[:MAX_MESSAGE]
        if not msg:
            return self._send_json({"error": "empty message"}, 400)
        device = (data.get("device") or "web").strip()[:40]
        from alontito.model import ModelUnavailable
        deltas = []

        def on_delta(token: str) -> None:
            deltas.append(token)
            BROKER.publish({"type": "chat_delta", "text": token})

        history = chat_history(int(CFG.get("history_limit", 12)))
        reply = AGENT.ask(msg, device=device, history=history,
                          on_delta=on_delta)
        answer = reply.get("text") or ""
        chat_save("user", msg)
        chat_save("assistant", answer)
        BROKER.publish({"type": "chat_done", "question": msg,
                        "answer": answer, "source": reply.get("source")})
        self._send_json({"answer": answer, "source": reply.get("source"),
                         "model": reply.get("model"),
                         "skill": reply.get("skill")})

    def _handle_pair(self, body: bytes) -> None:
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except Exception:
            data = {}
        verdict = PAIRING.verify(str(data.get("code") or ""))
        if not verdict.get("ok"):
            return self._send_json(verdict, 403)
        device = DEVICES.add(str(data.get("name") or "устройство"))
        self._send_json({"ok": True, "token": device["token"],
                         "id": device["id"], "name": device["name"]})

    def _add_automation(self, text: str) -> None:
        from alontito.automations import parse as parse_reminder
        auto = parse_reminder(text, time.time())
        if auto is None:
            return self._send_json(
                {"error": "не похоже на напоминание. Примеры: «напомни через "
                          "20 минут …», «каждый день в 9:00 …»"}, 400)
        stored = AGENT.bind().automations.add(auto)
        self._send_json({"ok": True, "item": stored.row()})

    def _run_automation(self, auto_id: str) -> None:
        agent = AGENT.bind()
        for a in agent.automations.list():
            if a.id == auto_id:
                BROKER.publish({"type": "notification", "kind": "reminder",
                                "text": a.text, "when": a.when_text(),
                                "automation_id": a.id, "manual": True})
                agent.automations.mark_fired(a.id, time.time())
                return self._send_json({"ok": True})
        return self._send_json({"error": "напоминание не найдено"}, 404)

    def _add_inbox(self, body: bytes) -> None:
        inbox = _inbox_safe()
        ctype = (self.headers.get("Content-Type") or "").lower()
        qs = urllib.parse.parse_qs(urllib.parse.urlparse("http://x" + self.path).query)
        note = (qs.get("note", [""])[0] or "")[:500]
        if "json" in ctype:
            try:
                data = json.loads(body.decode("utf-8") or "{}")
            except Exception:
                return self._send_json({"error": "bad json"}, 400)
            try:
                item = inbox.add("text", text=str(data.get("text") or ""),
                                 note=str(data.get("note") or note))
            except Exception as exc:
                return self._send_json({"error": str(exc)}, 400)
            return self._send_json({"ok": True, "item": item})
        kind = (qs.get("kind", ["file"])[0] or "file").lower()
        filename = (qs.get("filename", [""])[0] or "")[:120]
        try:
            item = inbox.add(kind, body, filename=filename, note=note)
        except Exception as exc:
            return self._send_json({"error": str(exc)}, 400)
        self._send_json({"ok": True, "item": item})

    def _send_inbox_file(self, item_id: str) -> None:
        inbox = _inbox_safe()
        item = inbox.get(item_id)
        data = inbox.read_file(item_id)
        if item is None or data is None:
            return self._send_json({"error": "not found"}, 404)
        ext = Path(item.get("filename") or "").suffix.lower()
        self._send_bytes(data, _MIME.get(ext, "application/octet-stream"))

    def _transcribe_inbox(self, item_id: str) -> None:
        inbox = _inbox_safe()
        item = inbox.get(item_id)
        if item is None:
            return self._send_json({"error": "not found"}, 404)
        if item["kind"] == "text":
            return self._send_json({"transcript": item.get("text")})
        result = transcribe_wav(inbox.read_file(item_id) or b"")
        if result.get("transcript"):
            inbox.set_state(item_id, item["state"],
                            transcript=result["transcript"])
        self._send_json(result)

    def _process_inbox(self, item_id: str) -> None:
        """Voice → чат агента; остальное — пометить обработанным."""
        inbox = _inbox_safe()
        item = inbox.get(item_id)
        if item is None:
            return self._send_json({"error": "not found"}, 404)
        text = item.get("transcript")
        if item["kind"] == "voice" and not text:
            result = transcribe_wav(inbox.read_file(item_id) or b"")
            text = result.get("transcript")
            if not text:
                return self._send_json({"ok": False, "detail": result.get("detail")})
        if item["kind"] == "text":
            text = item.get("text")
        if not text:
            return self._send_json(
                {"ok": False,
                 "detail": "понять содержимое агент сможет, когда подключим "
                           "его мозг со зрением; файл сохранён"},
                200)
        reply = AGENT.ask(text, device="inbox", history=[])
        inbox.set_state(item_id, "processed")
        self._send_json({"ok": True, "answer": reply.get("text"),
                         "source": reply.get("source")})

    def _add_fact(self, data: dict) -> None:
        text = (data.get("text") or "").strip()
        if not text:
            return self._send_json({"error": "empty fact"}, 400)
        from alontito.memory import STATUS_OFFICIAL
        source = (data.get("source") or "пользователь (устройство)").strip()[:100]
        fact = AGENT.bind().memory.add_fact(text, STATUS_OFFICIAL, source)
        self._send_json({"ok": True, "fact": fact})

    # ---- pet -----------------------------------------------------------

    def _send_pet_index(self) -> None:
        page = PET_WEB / "web.html"
        if not page.is_file():
            return self._send_json({"error": "pet page missing"}, 404)
        html = page.read_text(encoding="utf-8")
        from alontito.desktop_pet import import_map, vendor_ready
        imap = import_map(vendor_ready())
        imap = imap.replace("/vendor/", "/pet/vendor/")
        html = html.replace("{{IMPORTMAP}}", imap)
        self._send_bytes(html.encode("utf-8"), _MIME[".html"])

    def _send_pet_file(self, name: str) -> None:
        path = (PET_WEB / name)
        if not path.is_file():
            return self._send_json({"error": "not found"}, 404)
        ext = path.suffix.lower()
        cache = "max-age=3600" if name == "pet.js" else "no-store"
        self._send_bytes(path.read_bytes(), _MIME.get(ext, "application/octet-stream"),
                         cache=cache)

    def _send_pet_vendor(self, rel: str) -> None:
        base = (PET_WEB / "vendor").resolve()
        path = (base / rel).resolve()
        if not str(path).startswith(str(base)) or not path.is_file():
            return self._send_json({"error": "not found"}, 404)
        ext = path.suffix.lower()
        self._send_bytes(path.read_bytes(),
                         _MIME.get(ext, "application/octet-stream"),
                         cache="max-age=86400")

    def _send_pet_character(self) -> None:
        try:
            from alontito.desktop_pet import find_asset, load_config
            path = find_asset("character", load_config())
        except Exception:
            path = None
        if path is None or not path.is_file():
            return self._send_json({"error": "character model missing"}, 404)
        self._send_bytes(path.read_bytes(), _MIME[".glb"], cache="max-age=86400")

    # ---- static --------------------------------------------------------

    def _send_static(self, name: str) -> None:
        path = STATIC / name
        if not path.is_file():
            return self._send_json({"error": "missing static"}, 404)
        ctype = _MIME.get(path.suffix.lower()) or \
            mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        cache = "max-age=3600" if name.startswith("icons/") else "no-store"
        self._send_bytes(path.read_bytes(), ctype, cache=cache)

    # ---- SSE ------------------------------------------------------------

    def _sse(self):
        sub = BROKER.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self._safe_headers()
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            for ev in BROKER.snapshot_lines():
                self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    ev = sub.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": hb\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            BROKER.unsubscribe(sub)


def re_match(path: str, pattern: str):
    import re as _re
    return _re.match(pattern, path)


def _is_public(path: str) -> bool:
    """Cosmetic shell only: the app page, icons, the 3D pet and pairing.
    Everything with data (memory, chat, inbox, automations) needs a token."""
    return (path in ("/", "/index.html", "/app.js", "/style.css",
                     "/manifest.webmanifest", "/sw.js", "/api/pair")
            or path.startswith("/icons/")
            or path == "/pet"
            or path.startswith("/pet/"))


def journal_tail(n: int) -> list:
    lines = []
    try:
        if JOURNAL_FILE.exists():
            with JOURNAL_FILE.open("rb") as f:
                f.seek(max(0, JOURNAL_FILE.stat().st_size - MAX_LOG_TAIL))
                tail = f.read().decode("utf-8", errors="replace")
            lines = [l for l in tail.splitlines()[-n:] if l.strip()]
    except Exception:
        pass
    return lines


# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------

def main():
    AGENT.bind()
    threading.Thread(target=background_tasks, daemon=True).start()
    host, port = CFG.get("host", "0.0.0.0"), int(CFG.get("port", 8787))
    httpd = ThreadingHTTPServer((host, port), PultHandler)
    lan = host not in ("127.0.0.1", "localhost")
    print("=" * 62)
    print("   ALONTITO · приложение агента  (0 зависимостей)")
    print("=" * 62)
    print(f"   На этом ПК:   http://127.0.0.1:{port}/?token={TOKEN}")
    if lan:
        print(f"   С телефона:   http://{_lan_ip()}:{port}  → вкладка "
              f"«Настройки» → «Сопряжение» (мастер-токен или код с ПК)")
    else:
        print("   С телефона:   выключено (host=127.0.0.1). Откройте сеть "
              "в pult/pult_config.json: \"host\": \"0.0.0.0\"")
    print("   Ctrl+C — выход. Мастер-токен в pult/pult_config.json.")
    print("=" * 62)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[PULT] остановлен.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
