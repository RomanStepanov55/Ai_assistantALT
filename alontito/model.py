"""Model gateway: the ONLY door from Alontito to a language model.

Decision of 2026-11-27: the brain is OUR OWN local model (built by the user).
No third-party model ever answers the user. Three strictly separated roles:

  own      - the user's brain. Enabled explicitly in config/alontito_brain.json
             (gitignored: may hold a token for a local OpenAI-compatible
             server). While it is absent the agent runs in "collecting data"
             mode: skills answer without any model, and chat falls back to
             offline honest replies.
  teacher  - a downloaded local model that ONLY helps produce training
             examples for the own brain (tools/teacher_generate.py). The
             runtime chat path physically cannot reach it: chat() refuses
             the teacher role, and a test pins that.
  offline  - no model at all. Honest "не знаю" built from real status data,
             never an invented number.

Zero dependencies: urllib streams NDJSON (Ollama format) or SSE
(OpenAI-compatible format) from a LOCAL endpoint only. The config loader
refuses http(s) endpoints that are not loopback or a private LAN address, so
a config can never silently point at a cloud API.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
BRAIN_CONFIG = ROOT / "config" / "alontito_brain.json"
PROBE_TIMEOUT = 1.5
CHAT_TIMEOUT = 600


class ModelUnavailable(RuntimeError):
    """The own brain is disabled, unreachable or refused the request."""


class TeacherNotForRuntime(RuntimeError):
    """chat() cannot use the teacher: it exists only for training pipelines."""


def _is_local(url: str) -> bool:
    """Loopback or private LAN only - a cloud endpoint is refused by design."""
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").strip()
    except Exception:
        return False
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A hostname (e.g. the user's PC name). Resolve it and re-check.
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
        except (OSError, ValueError):
            return False
    return ip.is_private or ip.is_loopback


class BrainConfig:
    """Config for the own brain and the teacher. Absent file = everything off."""

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        data = data or {}
        own = data.get("own") or {}
        teacher = data.get("teacher") or {}
        self.own_enabled = bool(own.get("enabled", False))
        self.own_endpoint = str(own.get("endpoint") or "http://127.0.0.1:11434")
        self.own_api = str(own.get("api") or "ollama")          # "ollama" | "openai"
        self.own_model = str(own.get("model") or "alontito-brain")
        self.own_api_key = str(own.get("api_key") or "")
        self.own_max_context = int(own.get("max_context_messages", 16))
        self.teacher_enabled = bool(teacher.get("enabled", False))
        self.teacher_endpoint = str(teacher.get("endpoint") or "http://127.0.0.1:11434")
        self.teacher_model = str(teacher.get("model") or "")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "BrainConfig":
        p = Path(path) if path else BRAIN_CONFIG
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls(data if isinstance(data, dict) else {})

    def validated(self) -> "BrainConfig":
        """Refuse non-local endpoints before any request is made."""
        if not _is_local(self.own_endpoint):
            raise ModelUnavailable(
                f"own endpoint {self.own_endpoint} не локальный: облачные API "
                f"запрещены решением 2026-11-27 (мозг — свой и локальный)")
        if self.teacher_enabled and not _is_local(self.teacher_endpoint):
            raise ModelUnavailable(
                f"teacher endpoint {self.teacher_endpoint} не локальный")
        return self


class ModelGateway:
    """Runtime access to the own brain (and only to it)."""

    def __init__(self, config: Optional[BrainConfig] = None):
        self.cfg = (config or BrainConfig.load()).validated()

    # ------------------------------------------------------------- probing

    def own_status(self) -> Dict[str, Any]:
        st = {
            "enabled": self.cfg.own_enabled,
            "endpoint": self.cfg.own_endpoint,
            "api": self.cfg.own_api,
            "model": self.cfg.own_model,
            "reachable": False,
            "detail": "",
        }
        if not self.cfg.own_enabled:
            st["detail"] = "выключен в config/alontito_brain.json"
            return st
        base = self.cfg.own_endpoint.rstrip("/")
        try:
            path = "/api/tags" if self.cfg.own_api == "ollama" else "/v1/models"
            with urllib.request.urlopen(base + path, timeout=PROBE_TIMEOUT) as r:
                payload = json.loads(r.read().decode("utf-8", "replace"))
            names = ([m.get("name", "") for m in payload.get("models", [])]
                     if self.cfg.own_api == "ollama"
                     else [m.get("id", "") for m in payload.get("data", [])])
            st["reachable"] = True
            st["models"] = names
            st["model_present"] = any(self.cfg.own_model in n for n in names)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            st["detail"] = str(exc)
        return st

    # --------------------------------------------------------------- chat

    def chat(self, messages: List[Dict[str, str]], *,
             role: str = "own",
             on_delta: Optional[Callable[[str], None]] = None,
             transport: Optional[Callable[[str, bytes, Dict[str, str]], Any]] = None) -> Dict[str, Any]:
        """One chat completion against the own brain, streamed.

        Raises ModelUnavailable when the brain is off/unreachable and
        TeacherNotForRuntime if anyone tries to route chat through the
        teacher (it exists only for the training pipeline). transport is
        injectable for tests.
        """
        if role == "teacher":
            raise TeacherNotForRuntime(
                "учитель не отвечает пользователю: он нужен только конвейеру "
                "обучения (tools/teacher_generate.py)")
        if role != "own":
            raise ValueError(f"unknown model role: {role}")
        if not self.cfg.own_enabled:
            raise ModelUnavailable("свой мозг не подключён: "
                                   "config/alontito_brain.json → own.enabled")
        base = self.cfg.own_endpoint.rstrip("/")
        if self.cfg.own_api == "ollama":
            url = base + "/api/chat"
            payload = {"model": self.cfg.own_model, "messages": messages,
                       "stream": True}
            reader = self._read_ollama
        else:
            url = base + "/v1/chat/completions"
            payload = {"model": self.cfg.own_model, "messages": messages,
                       "stream": True}
            reader = self._read_openai
        headers = {"Content-Type": "application/json"}
        if self.cfg.own_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.own_api_key}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        opener = transport or self._open
        with opener(url, body, headers) as resp:
            text = reader(resp, on_delta)
        return {"text": text, "provider": "own", "model": self.cfg.own_model}

    # ------------------------------------------------------------ internals

    @staticmethod
    def _open(url: str, body: bytes, headers: Dict[str, str]):
        req = urllib.request.Request(url, data=body, headers=headers)
        return urllib.request.urlopen(req, timeout=CHAT_TIMEOUT)

    @staticmethod
    def _read_ollama(resp, on_delta) -> str:
        text = ""
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except ValueError:
                continue
            token = (chunk.get("message") or {}).get("content") or ""
            if token:
                text += token
                if on_delta:
                    on_delta(token)
            if chunk.get("done"):
                break
        return text

    @staticmethod
    def _read_openai(resp, on_delta) -> str:
        text = ""
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            token = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
            if token:
                text += token
                if on_delta:
                    on_delta(token)
        return text


class Teacher:
    """Training-pipeline access to the downloaded teacher model.

    This class is imported by tools/teacher_generate.py only. It is NOT
    reachable from chat(): the runtime gateway has no path here, and
    tests/test_model.py pins that separation.
    """

    def __init__(self, config: Optional[BrainConfig] = None):
        cfg = (config or BrainConfig.load()).validated()
        if not cfg.teacher_enabled or not cfg.teacher_model:
            raise ModelUnavailable(
                "учитель не настроен: config/alontito_brain.json → teacher")
        if not _is_local(cfg.teacher_endpoint):
            raise ModelUnavailable("учитель должен быть локальным")
        self.endpoint = cfg.teacher_endpoint.rstrip("/")
        self.model = cfg.teacher_model

    def status(self) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(self.endpoint + "/api/tags",
                                        timeout=PROBE_TIMEOUT) as r:
                names = [m.get("name", "") for m in json.loads(
                    r.read().decode("utf-8", "replace")).get("models", [])]
            return {"enabled": True, "model": self.model,
                    "reachable": True,
                    "model_present": any(self.model in n for n in names)}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"enabled": True, "model": self.model,
                    "reachable": False, "detail": str(exc)}

    def complete(self, prompt: str, *, system: str = "",
                 transport: Optional[Callable[[str, bytes, Dict[str, str]], Any]] = None) -> str:
        """One blocking completion. Used for generating training examples."""
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        payload = json.dumps({"model": self.model, "messages": messages,
                              "stream": False}, ensure_ascii=False).encode("utf-8")
        opener = transport or ModelGateway._open
        req_url = self.endpoint + "/api/chat"
        headers = {"Content-Type": "application/json"}
        with opener(req_url, payload, headers) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return (data.get("message") or {}).get("content") or ""


# ---------------------------------------------------------------------------
# offline: honest answers with no model at all
# ---------------------------------------------------------------------------

def offline_reply(user_text: str, status: Optional[Dict[str, Any]] = None) -> str:
    """A reply built from REAL status data only. Never invents.

    The offline mode exists so the app is useful (skills, reminders, memory)
    while the own brain is being built. It never pretends to know something.
    """
    st = status or {}
    low = (user_text or "").lower()
    own = st.get("own") or {}
    brain_state = ("мозг подключён" if own.get("enabled") and own.get("reachable")
                   else "свой мозг ещё не подключён — я собираю данные для его обучения")
    if any(w in low for w in ("привет", "здравств", "hi", "hello")):
        return ("Привет! Я Алонтито. Мозг: " + brain_state +
                ". Пока я работаю навыками: «запомни: …», «напомни …», "
                "«статус», «что мы решали про …».")
    if "статус" in low or "status" in low:
        facts = st.get("facts")
        sessions = st.get("sessions")
        parts = [f"статус: {brain_state}"]
        if sessions is not None:
            parts.append(f"сессий {sessions}")
        if facts is not None:
            parts.append(f"фактов {facts}")
        return ". ".join(parts) + "."
    if "умеешь" in low or "помощь" in low or "help" in low:
        return ("Без мозга я умею: запоминать факты («запомни: …»), "
                "напоминания («напомни завтра в 9:00 …»), показывать память "
                "и журнал. Ответы «по смыслу» появятся, когда подключим твой "
                "мозг; мои ответы пойдут на его обучение.")
    # Honest refusal - no invention, no made-up knowledge.
    return ("Свой мозг ещё не подключён, поэтому рассуждать по смыслу я "
            "пока не могу — и не буду выдумывать ответ. " + brain_state +
            ". Что я могу без модели: «запомни: …», «напомни …», «статус», "
            "память и журнал.")
