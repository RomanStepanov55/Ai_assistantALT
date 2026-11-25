#!/usr/bin/env python3
"""
VoiceChat v5 — голосовое общение с Алонтито.

Конвейер: STT (Vosk, офлайн) -> мозг (Ollama локально) -> TTS (pyttsx3, офлайн).
Всё локально, без платных API и без облаков: распознавание через облачный
Google-сервер (recognize_google) из исходного варианта удалено — микрофонный
аудио не должно покидать машину. Vosk или его модель отсутствуют -> голосового
входа нет, но текстовые команды (handle_text) работают всегда.

Голосовые фразы (можно и текстом в консоль):
    "статус" / "отчёт"          -> сводка состояния
    всё остальное               -> свободный диалог через Ollama

Игровые команды из исходного варианта v5 ("стоп", "старт", "наблюдатель",
"тренировка", "эксперимент") удалены вместе с правом агента играть: голос не
может запустить тренировку или паузу игрового цикла (HARD RULE #2, 24.11.2026).

Интеграция:
    from brain.voice_chat import VoiceInterface
    vi = VoiceInterface(on_command=my_handler, ollama_model="qwen2.5:7b-instruct")
    vi.start_background()        # поток: слушает и отвечает голосом
    # или без потока:
    reply = vi.handle_text("какая у меня сила?")
"""
from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path
from typing import Callable, Optional


class SttEngine:
    """Vosk офлайн. Модель ожидается в data/models/vosk-small-ru.
    Нет пакета или модели -> голосового входа нет (ok=False), это честный отказ."""

    def __init__(self, sample_rate: int = 16000):
        self.ok = False
        self._model = None
        self._rec_type = None
        self.sample_rate = sample_rate
        try:
            from vosk import Model, KaldiRecognizer  # type: ignore
            model_path = Path(__file__).resolve().parent.parent / "data" / "models" / "vosk-small-ru"
            if model_path.exists():
                self._model = Model(str(model_path))
                self._rec_type = KaldiRecognizer
                self.ok = True
        except Exception:
            self.ok = False

    def listen(self, timeout: int = 6) -> Optional[str]:
        if not self.ok:
            return None
        return self._listen_vosk(timeout)

    def _listen_vosk(self, timeout: int) -> Optional[str]:
        import sounddevice as sd  # type: ignore
        rec = self._rec_type(self._model, self.sample_rate)
        with sd.RawInputStream(samplerate=self.sample_rate, blocksize=8000, dtype="int16",
                               channels=1) as stream:
            import time
            t0 = time.time()
            while time.time() - t0 < timeout:
                data, _ = stream.read(8000)
                if rec.AcceptWaveform(bytes(data)):
                    res = json.loads(rec.Result())
                    text = res.get("text", "").strip()
                    if text:
                        return text
        return None


class TtsEngine:
    """pyttsx3 — офлайн TTS. Есть только если установлен.
    Поток-слушатель не главный: COM на Windows требует CoInitialize в потоке."""

    def __init__(self):
        self.ok = False
        self._engine = None
        try:
            import os
            if os.name == "nt":
                try:
                    import comtypes  # type: ignore
                    comtypes.CoInitialize()
                except Exception:
                    pass
            import pyttsx3  # type: ignore
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", 165)
            self.ok = True
        except Exception:
            self.ok = False

    def say(self, text: str) -> None:
        if not self.ok:
            print(f"[VOICE->TEXT] {text}")
            return
        self._engine.say(text)
        self._engine.runAndWait()


class OllamaClient:
    """Только локальный Ollama (правило проекта: никаких платных API)."""

    def __init__(self, model: str = "qwen2.5:7b-instruct", host: str = "http://127.0.0.1:11434"):
        self.model, self.host = model, host

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=1.5) as r:
                return r.status == 200
        except Exception:
            return False

    def ask(self, prompt: str, system: Optional[str] = None) -> str:
        payload = {"model": self.model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        req = urllib.request.Request(f"{self.host}/api/generate",
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read()).get("response", "").strip()
        except Exception:
            return ""       # сервер упал/модели нет - вызывающий уйдёт в фолбэк

    def has_model(self) -> bool:
        """Есть ли запрошенная модель на сервере, а не просто живой сервер."""
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=1.5) as r:
                models = json.loads(r.read()).get("models", [])
            return any(m.get("name", "").startswith(self.model) for m in models)
        except Exception:
            return False


class VoiceInterface:
    COMMANDS = ("статус", "отчёт")

    def __init__(self, on_command: Optional[Callable[[str, str], str]] = None,
                 ollama_model: str = "qwen2.5:7b-instruct", speak: bool = True,
                 stt_enable: bool = True):
        self.on_command = on_command
        self.stt = SttEngine() if stt_enable else None
        self.tts = TtsEngine() if speak else None
        self.llm = OllamaClient(model=ollama_model)
        self.listening = False

    # ---------- текстовый вход (тестируемо без микрофона) ----------
    def handle_text(self, text: str) -> str:
        text = text.strip()
        low = text.lower()
        for cmd in self.COMMANDS:
            if low.startswith(cmd):
                arg = text[len(cmd):].strip()
                return self._route(cmd, arg, original=text)
        return self._chat(text)

    def _route(self, cmd: str, arg: str, original: str) -> str:
        if self.on_command:
            try:
                reply = self.on_command(cmd, arg)
                if reply:
                    return reply
            except Exception as e:
                return f"Ошибка команды '{cmd}': {e}"
        fallback = {
            "статус": "Статус: агент активен (подключите on_command для реальных данных).",
            "отчёт": "Отчёт недоступен: не подключён обработчик команд.",
        }
        return fallback.get(cmd, original)

    def _chat(self, text: str) -> str:
        # точные фразы отвечаются без модели: сервер может быть жив, но с 0
        # моделей (замерено на этой машине 22.11: Ollama отвечает, models=[])
        heur = {"привет": "Привет! Я Alontito. Голосовые модули готовы, "
                           "подключи Ollama для свободного диалога.",
                "как дела": "Все системы в норме. Жду приказаний.",
                "кто ты": "Я открытый AI-агент и 3D-питомец. Учусь, тренируюсь и охраняю твой рабочий стол."}
        if text.lower() in heur:
            return heur[text.lower()]
        if self.llm.available() and self.llm.has_model():
            reply = self.llm.ask(text, system=(
                "Ты — Alontito, AI-ассистент на рабочем столе. Отвечай кратко, "
                "по-русски, дружелюбно, с лёгким киберпанк-настроением."))
            if reply:
                return reply
        return heur.get(text.lower(), "Свободный диалог ограничен: локальных "
                                      "моделей нет (Ollama пуст). Сводка — "
                                      "«статус» или «отчёт».")

    # ---------- голосовой цикл ----------
    def listen_once(self, timeout: int = 6) -> Optional[str]:
        if self.stt is None:
            return None
        return self.stt.listen(timeout=timeout)

    def say(self, text: str) -> None:
        if self.tts:
            self.tts.say(text)
        else:
            print(f"[VOICE] {text}")

    def run_loop(self, idle_sleep: float = 0.3) -> None:
        self.listening = True
        if self.stt is None or not self.stt.ok:
            self.say("Голосовой вход недоступен: нужен пакет vosk и модель "
                     "data/models/vosk-small-ru. Текстовые команды работают.")
            self.listening = False
            return
        self.say("Alontito на связи. Слушаю.")
        while self.listening:
            heard = self.listen_once()
            if heard:
                reply = self.handle_text(heard)
                print(f"[YOU] {heard}\n[ALO] {reply}")
                self.say(reply)
            import time
            time.sleep(idle_sleep)

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.run_loop, daemon=True, name="voice-chat")
        t.start()
        return t

    def stop(self) -> None:
        self.listening = False
