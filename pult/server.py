#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alontito Pult — веб-пульт companion'а (телефон + ПК), zero-dependency.

Запуск из корня репозитория:
    python pult/server.py            # или двойной клик по Pult.bat

Открыть в браузере ПК:   http://127.0.0.1:8787/?token=<TOKEN>
С телефона (одна Wi-Fi сеть): http://<IP-ПК>:8787/?token=<TOKEN>
  — только если в pult/pult_config.json явно задано "host": "0.0.0.0".

Токен генерируется при первом запуске и хранится в pult_config.json рядом с
этим файлом (файл в .gitignore: токен не должен попадать в репозиторий).

HARD RULE #2 (решение пользователя, 2026-11-24): агент НЕ играет за
пользователя. Пульт только читает: статус, наблюдение сцены, журнал, память,
база знаний и чат. Эндпоинтов действий (тренировка, закрытие диалога, команды
автономному циклу) здесь нет — ввод в игру отключён на уровне InputController.

Зависимостей нет: http.server + SSE вместо WebSocket, urllib вместо requests,
sqlite3 для истории чата. Чат отвечает через локальную Ollama; если Ollama не
запущена — работает автономный ответчик по статусу.
"""
from __future__ import annotations

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
    sys.path.insert(0, str(ROOT))  # make `alontito.*`, `games.*`, `pult.*` importable
STATIC = HERE / "static"
DATA = ROOT / "data"
# Путь конфига можно переопределить (тесты пишут во временную папку)
CONFIG_FILE = Path(os.environ.get("ALONTITO_PULT_CONFIG") or (HERE / "pult_config.json"))
CHAT_DB = DATA / "pult_chat.sqlite3"
FRAME_FILE = DATA / "pult_frame.png"      # свежий кадр окна игры
FRAME_MAX_AGE = 30.0                      # старше — «кадра нет», а не прошлогодний
MAX_BODY = 64 * 1024                      # лимит тела запроса
MAX_MESSAGE = 2000                        # лимит сообщения чата
MAX_LOG_TAIL = 256 * 1024                 # байт с конца журнала за одно чтение

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
    cfg = {
        # Только этот ПК. Пульт показывает кадр экрана и историю чата, поэтому
        # локальную сеть открываем явным решением, а не по умолчанию.
        "host": "127.0.0.1",
        "port": 8787,
        "token": None,
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "qwen2.5:7b-instruct",
        "knowledge_dir": str(ROOT.parent / "alontito-knowledge"),
        "history_limit": 12,
    }
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[PULT] config unreadable, using defaults: {e}")
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(12)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return cfg


CFG = load_config()
TOKEN = CFG["token"]

# --------------------------------------------------------------------------
# Agent bindings (lazy — the pult boots even without Roblox/vision stack)
# --------------------------------------------------------------------------

class Agent:
    def __init__(self):
        self.bot = None
        self.memory = None
        self._lock = threading.Lock()
        self.attached = False
        self.last_health: dict = {}
        self.telemetry: list = []          # ring of {t, strength, gems}
        self.games: list = []

    def bind(self):
        with self._lock:
            if self.bot is not None:
                return self
            try:
                from alontito.skills import AlontitoBot
                from alontito.memory import Memory
                self.bot = AlontitoBot()
                self.memory = Memory()
            except Exception as e:
                print(f"[PULT] agent bindings unavailable: {e}")
                self.bot = None
                self.memory = None
            try:
                from games.registry import available_games
                self.games = available_games()
            except Exception:
                self.games = ["muscle_legends"]
        return self

    # Единственные навыки пульта — читающие. Список действий намеренно закрыт.
    READ_ONLY_SKILLS = ("health", "observe")

    def run_skill(self, name: str, **kwargs) -> dict:
        self.bind()
        if name not in self.READ_ONLY_SKILLS:
            return {"ok": False, "error": f"action '{name}' not allowed"}
        if self.bot is None:
            return {"ok": False, "error": "стек агента недоступен (импорт не удался)"}
        with self._lock:
            try:
                r = getattr(self.bot, name)()      # health() / observe(): чтение
                self.attached = bool(r.success) or self.bot.is_attached()
                return {"ok": bool(r.success), "name": r.name, "detail": r.detail,
                        "reward": r.reward, "seconds": round(r.seconds, 1),
                        "metrics": r.metrics, "rows": r.rows, "error": r.error}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    def snapshot(self) -> dict:
        self.bind()
        mem_summary = {}
        if self.memory is not None:
            try:
                d = self.memory.data
                mem_summary = {
                    "sessions": d.get("sessions", 0),
                    "total_strength_gain": d.get("total_strength_gain", 0),
                    "facts": len(d.get("facts", [])),
                    "questions_open": len(self.memory.open_questions()),
                    "selected_game": (d.get("selected_game") or {}).get("name"),
                    "skills": [{"name": s.name, "attempts": s.attempts,
                                "success_rate": s.success_rate,
                                "per_minute": s.per_minute} for s in self.memory.skills()],
                }
            except Exception:
                pass
        return {
            "time": time.strftime("%H:%M:%S"),
            "attached": self.attached,
            "last_health": self.last_health,
            "telemetry": self.telemetry[-120:],
            "memory": mem_summary,
            "games": self.games,
            "frame_age": frame_age(),
        }


AGENT = Agent()


def frame_age():
    """Возраст последнего кадра в секундах или None, если его нет."""
    try:
        return round(time.time() - FRAME_FILE.stat().st_mtime, 1)
    except OSError:
        return None


def save_frame() -> None:
    """Свежий кадр окна игры для вкладки «Панель». Только чтение экрана:
    окно не поднимается, ничего не нажимается."""
    bot = AGENT.bot
    if bot is None or getattr(bot, "eye", None) is None:
        return
    try:
        frame = bot.eye.capture()
        if frame is not None:
            DATA.mkdir(parents=True, exist_ok=True)
            frame.save(FRAME_FILE, format="PNG")
    except Exception:
        pass


# --------------------------------------------------------------------------
# Knowledge base (alontito-knowledge clone, read-only)
# --------------------------------------------------------------------------

def read_knowledge() -> dict:
    kd = Path(CFG.get("knowledge_dir") or "")
    out = {"dir": str(kd), "lessons": [], "skills": None, "facts": [],
           "lessons_total": 0, "ok": False}
    try:
        lessons_file = kd / "knowledge" / "lessons.jsonl"
        if lessons_file.exists():
            lines = lessons_file.read_text(encoding="utf-8", errors="replace").splitlines()
            out["lessons_total"] = len([l for l in lines if l.strip()])
            for line in lines[-20:]:
                line = line.strip()
                if line:
                    try:
                        out["lessons"].append(json.loads(line))
                    except Exception:
                        pass
        skills_file = kd / "skills" / "skills.json"
        if skills_file.exists():
            out["skills"] = json.loads(skills_file.read_text(encoding="utf-8"))
        facts_file = kd / "facts" / "facts.jsonl"
        if facts_file.exists():
            for line in facts_file.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]:
                line = line.strip()
                if line:
                    try:
                        out["facts"].append(json.loads(line))
                    except Exception:
                        pass
        out["ok"] = True
    except Exception as e:
        out["error"] = str(e)
    return out


# --------------------------------------------------------------------------
# Chat (Ollama + sqlite history + read-only intents)
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """Ты — Алонтито, наблюдательный AI-компаньон на рабочем столе.
Ты НЕ играешь за пользователя: не нажимаешь клавиш, не кликаешь, не тратишь
ресурсы — ввод в игру отключён на уровне кода. Ты смотришь на игру через
захват экрана и чтение HUD, помнишь прошлые ситуации и решения, отвечаешь на
вопросы и напоминаешь о прошлом по журналу и памяти.
Твоя этика жёсткая: никаких покупок за реальные деньги/Robux, никаких трейдов,
никаких обещаний действий, которые ты не можешь выполнить.
Отвечай коротко и по делу, на русском. Если пользователь просит сыграть или
потренировать персонажа — объясни, что ты не играешь за него, и предложи то,
что умеешь: «статус», «что видишь». Никогда не выдумывай числа: если замера
нет, скажи «не подтверждено»."""


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


def ollama_chat(messages: list) -> str:
    url = CFG["ollama_url"].rstrip("/") + "/api/chat"
    payload = json.dumps({
        "model": CFG["ollama_model"],
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.4},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return (data.get("message") or {}).get("content") or "..."


def offline_reply(user_text: str) -> str:
    snap = AGENT.snapshot()
    att = "вижу окно игры" if snap.get("attached") else "окно игры не найдено"
    low = user_text.lower()
    if any(w in low for w in ("привет", "здравств", "hi", "hello")):
        return (f"Привет! Я Алонтито. Сейчас {att}. Я не играю за тебя — только "
                "наблюдаю и подсказываю. Могу показать статус или прочитать "
                "сцену. Что интересует?")
    if "статус" in low or "status" in low:
        m = snap.get("memory") or {}
        return (f"Статус: {att}. Сессий: {m.get('sessions', 0)}, "
                f"всего силы накоплено: {m.get('total_strength_gain', 0):,}. "
                f"Фактов в памяти: {m.get('facts', 0)}.")
    if "умеешь" in low or "help" in low or "команды" in low:
        return ("Я умею: «статус» — что сейчас в игре, «что видишь» — прочитать "
                "сцену; история и память — на вкладках пульта. Играть, "
                "тренировать персонажа и закрывать диалоги я не могу: "
                "ввод в игру отключён.")
    if any(w in low for w in ("потрен", "тренировк", "подход", "гантел",
                              "поиграй", "играй за", "качай")):
        return ("Я не играю за тебя и не тренирую персонажа: ввод в игру "
                "отключён на уровне кода. Могу наблюдать и подсказывать.")
    if any(w in low for w in ("закрой", "закрыть", "кликни", "нажми")):
        return ("Я ничего не кликаю и не нажимаю: ввод в игру отключён. "
                "Могу только смотреть и советовать.")
    return (f"Я в режиме офлайн (Ollama не отвечает). Статус: {att}. "
            "Спроси «что ты умеешь?» или «статус» — всё считается "
            "локально, без облаков.")


# Единственное намерение — чтение. Намерений «поиграть/закрыть/пауза» нет:
# пульт не может расширить круг действий агента.
INTENTS = [
    ("status", ("статус", "status", "что видишь", "что происходит", "наблюд")),
]


def detect_intent(text: str):
    low = text.lower()
    for name, needles in INTENTS:
        if any(n in low for n in needles):
            return name
    return None


def run_intent(name: str) -> str:
    if name == "status":
        r = AGENT.run_skill("observe")
        if r.get("ok"):
            met = r.get("metrics") or {}
            bits = []
            if met.get("strength") is not None:
                bits.append(f"сила {met['strength']:,}")
            if met.get("gems") is not None:
                bits.append(f"гемы {met['gems']:,}")
            if met.get("modal_open"):
                bits.append("открыт диалог")
            return "Вижу: " + (" · ".join(bits) if bits else r.get("detail", ""))
        return f"Наблюдение не удалось: {r.get('detail') or r.get('error')}"
    return "Неизвестное действие."


def handle_chat(user_text: str) -> str:
    intent = detect_intent(user_text)
    prefix = ""
    if intent:
        prefix = run_intent(intent) + "\n\n"
    history = chat_history(int(CFG.get("history_limit", 12)))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history
    messages.append({"role": "user", "content": user_text})
    try:
        answer = ollama_chat(messages)
    except Exception as e:
        print(f"[PULT] ollama unavailable ({e}); offline reply")
        answer = offline_reply(user_text)
    chat_save("user", user_text)
    chat_save("assistant", answer)
    return prefix + answer


# --------------------------------------------------------------------------
# SSE broker (live logs + status)
# --------------------------------------------------------------------------

class Broker:
    def __init__(self):
        self.subs: list = []
        self._lock = threading.Lock()
        self._offsets: dict = {}

    def subscribe(self):
        q = queue.Queue(maxsize=200)
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
        """Хвосты журналов без смещений: первичная заливка нового SSE-клиента.
        Не трогает offsets инкрементального стримера."""
        events = []
        for path in (DATA / "alontito" / "journal.jsonl",
                     DATA / "eye_journal.log",
                     DATA / "telemetry.log"):
            try:
                if not path.exists():
                    continue
                with path.open("rb") as f:
                    f.seek(max(0, path.stat().st_size - MAX_LOG_TAIL))
                    tail = f.read().decode("utf-8", errors="replace")
                for line in tail.splitlines()[-20:]:
                    line = line.strip()
                    if line:
                        events.append({"type": "log", "source": path.name,
                                       "line": line[-400:]})
            except Exception:
                pass
        return events

    def tail_logs(self) -> list:
        events = []
        for path in (DATA / "alontito" / "journal.jsonl",
                     DATA / "eye_journal.log",
                     DATA / "telemetry.log"):
            try:
                if not path.exists():
                    continue
                size = path.stat().st_size
                last = self._offsets.get(str(path), 0)
                if size < last:
                    last = 0
                if size == last:
                    continue
                # при первом чтении большого журнала берём только хвост
                start = min(last, max(0, size - MAX_LOG_TAIL))
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    f.seek(start)
                    for line in f.readlines()[-30:]:
                        line = line.strip()
                        if line:
                            events.append({"type": "log", "source": path.name,
                                           "line": line[-400:]})
                self._offsets[str(path)] = size
            except Exception:
                pass
        return events


BROKER = Broker()


def background_tasks():
    """Sampler: health-check the agent, feed telemetry ring, broadcast SSE."""
    while True:
        try:
            r = AGENT.run_skill("health")
            AGENT.last_health = r
            met = r.get("metrics") or {}
            if met.get("strength") is not None:
                AGENT.telemetry.append({
                    "t": int(time.time()),
                    "strength": met.get("strength"),
                    "gems": met.get("gems"),
                    "conf": met.get("strength_conf"),
                })
                AGENT.telemetry[:] = AGENT.telemetry[-240:]
                save_frame()
            BROKER.publish({"type": "status", "status": AGENT.snapshot()})
            for ev in BROKER.tail_logs():
                BROKER.publish(ev)
        except Exception as e:
            print(f"[PULT] sampler error: {e}")
        time.sleep(5.0)


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

class BodyTooLarge(Exception):
    pass


class PultHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AlontitoPult/1.0"

    # ---- helpers -----------------------------------------------------

    def log_message(self, fmt, *args):  # quiet console: токен не должен попадать в лог
        pass

    def _authorized(self) -> bool:
        if self.path.split("?")[0] in ("/", "/index.html", "/style.css", "/app.js"):
            return True  # static shell is harmless; data needs the token
        query = urllib.parse.urlparse("http://x" + self.path).query
        from urllib.parse import parse_qs
        qtoken = parse_qs(query).get("token", [None])[0]
        header = self.headers.get("X-Pult-Token", "")
        # байты, а не str: compare_digest бросает TypeError на не-ASCII
        return secrets.compare_digest((header or qtoken or "").encode("utf-8"),
                                      TOKEN.encode("utf-8"))

    def _safe_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")   # токен в URL не утекает

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._safe_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length > MAX_BODY:
            raise BodyTooLarge()
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    # ---- GET ---------------------------------------------------------

    def do_GET(self):
        if not self._authorized():
            return self._send_json({"error": "unauthorized"}, 401)

        url = urllib.parse.urlparse("http://x" + self.path)
        path = url.path
        from urllib.parse import parse_qs
        qs = parse_qs(url.query)

        if path == "/" or path == "/index.html":
            return self._send_static("index.html")
        if path in ("/style.css", "/app.js"):
            return self._send_static(path.lstrip("/"))
        if path == "/api/status":
            return self._send_json(AGENT.snapshot())
        if path == "/api/frame":
            return self._send_frame()
        if path == "/api/memory":
            return self._send_json(AGENT.snapshot().get("memory") or {})
        if path == "/api/knowledge":
            return self._send_json(read_knowledge())
        if path == "/api/chat/history":
            return self._send_json({"messages": chat_history(50)})
        if path == "/api/journal":
            try:
                n = int(qs.get("n", [100])[0])
            except ValueError:
                n = 100
            return self._send_json(self._journal_tail(max(1, min(n, 1000))))
        if path == "/api/events":
            return self._sse()
        return self._send_json({"error": "not found"}, 404)

    # ---- POST --------------------------------------------------------

    def do_POST(self):
        if not self._authorized():
            return self._send_json({"error": "unauthorized"}, 401)
        url = urllib.parse.urlparse("http://x" + self.path)
        try:
            body = self._read_body()
        except BodyTooLarge:
            return self._send_json({"error": "body too large"}, 413)

        if url.path == "/api/chat":
            msg = (body.get("message") or "").strip()[:MAX_MESSAGE]
            if not msg:
                return self._send_json({"error": "empty message"}, 400)
            answer = handle_chat(msg)
            return self._send_json({"answer": answer})

        # Единственный эндпоинт действий — и тот только читающий.
        if url.path == "/api/action":
            name = body.get("name")
            if name not in Agent.READ_ONLY_SKILLS:
                return self._send_json({"error": f"action '{name}' not allowed"}, 403)
            result = AGENT.run_skill(name)
            return self._send_json(result)

        return self._send_json({"error": "not found"}, 404)

    # ---- senders -----------------------------------------------------

    def _send_static(self, name: str):
        path = STATIC / name
        if not path.is_file():
            return self._send_json({"error": "missing static"}, 404)
        body = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8"
                         if ctype.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self._safe_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_frame(self):
        age = frame_age()
        if age is None or age > FRAME_MAX_AGE:
            return self._send_json({"error": "no fresh frame (game window not visible)"}, 404)
        body = FRAME_FILE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._safe_headers()
        self.end_headers()
        self.wfile.write(body)

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
            # первичная заливка: хвосты журналов, чтобы вкладка «Терминал»
            # не открывалась пустой при молчащем агенте
            try:
                for ev in BROKER.snapshot_lines():
                    payload = json.dumps(ev, ensure_ascii=False, default=str)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            except Exception:
                pass
            self.wfile.flush()
            while True:
                try:
                    ev = sub.get(timeout=15)
                    payload = json.dumps(ev, ensure_ascii=False, default=str)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": hb\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            BROKER.unsubscribe(sub)

    def _journal_tail(self, n: int) -> dict:
        lines: list = []
        for path in (DATA / "alontito" / "journal.jsonl",
                     DATA / "eye_journal.log"):
            try:
                if path.exists():
                    with path.open("rb") as f:
                        f.seek(max(0, path.stat().st_size - MAX_LOG_TAIL))
                        tail = f.read().decode("utf-8", errors="replace")
                    for line in tail.splitlines()[-n:]:
                        if line.strip():
                            lines.append({"source": path.name, "line": line[:500]})
            except Exception:
                pass
        return {"lines": lines[-n:]}


# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------

def main():
    AGENT.bind()
    threading.Thread(target=background_tasks, daemon=True).start()
    host, port = CFG.get("host", "127.0.0.1"), int(CFG.get("port", 8787))
    httpd = ThreadingHTTPServer((host, port), PultHandler)
    lan = host not in ("127.0.0.1", "localhost")
    print("=" * 62)
    print("   ALONTITO PULT v1.0  (zero-dependency, только наблюдение)")
    print("=" * 62)
    print(f"   На этом ПК:   http://127.0.0.1:{port}/?token={TOKEN}")
    if lan:
        print(f"   С телефона:   http://{_lan_ip()}:{port}/?token={TOKEN}   (одна Wi-Fi сеть)")
    else:
        print("   С телефона:   выключено. Пульт показывает кадр экрана и чат, поэтому")
        print("                 локальную сеть открывают явно: в pult/pult_config.json")
        print('                 задайте "host": "0.0.0.0" и перезапустите пульт.')
    print(f"   Ollama:       {CFG['ollama_url']}  модель: {CFG['ollama_model']}")
    print("   Ctrl+C — выход. Токен хранится в pult_config.json.")
    print("=" * 62)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[PULT] остановлен.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
