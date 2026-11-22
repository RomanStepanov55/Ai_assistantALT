"""Чат с Алонтито: локальная Ollama, при её отсутствии — честный локальный ответчик."""
from __future__ import annotations
import json
import urllib.request

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config import OLLAMA_URL, PROJECT_ROOT
from backend.services.agent_bridge import bridge
from backend.services import telemetry_reader as tr

router = APIRouter(prefix="/api", tags=["chat"])


class Msg(BaseModel):
    message: str


def _pick_model():
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=2) as r:
            tags = json.loads(r.read().decode("utf-8"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        for pref in ("alontito", "qwen2.5", "deepseek", "qwen"):
            for n in names:
                if pref in n.lower():
                    return n
        return names[0] if names else None
    except Exception:
        return None


def _local_reply(text: str) -> str:
    t = text.lower()
    st = bridge.status()
    last = tr.last_sample()
    if any(k in t for k in ("статус", "состоян", "как ты")):
        s = "работаю" if st["running"] else "остановлен"
        extra = f" Последний замер: сила {last['strength']:,} ({last['ts']})." if last else ""
        return f"Я {s}.{extra} Нажми «Запустить», чтобы я начал тренировку."
    if any(k in t for k in ("сила", "прогресс", "темп")):
        if last:
            return f"По архиву telemetry.log: сила {last['strength']:,}, темп +{last['rate']}/мин, ребёрнов {last['rebirths']}. Это последний записанный замер ({last['ts']}), не live."
        return "Замеров пока нет. Запусти агента, чтобы я начал собирать телеметрию."
    if any(k in t for k in ("урок", "научил", "памят")):
        ins = tr.learning().get("insights") or []
        return "Мои уроки: " + ("; ".join(ins[-3:]) if ins else "пока пусто.")
    if any(k in t for k in ("запуст", "старт", "игра")):
        return "Кнопка «Запустить» сама откроет Roblox и Muscle Legends, затем начнёт цикл. Следи за панелью «Поток агента»."
    return ("Я Алонтито, локальный агент. Ollama сейчас недоступна, поэтому отвечаю встроенным ответчиком. "
            "Спроси про статус, силу, уроки или запуск — либо включи Ollama для полноценного диалога.")


@router.post("/chat")
def chat(m: Msg):
    model = _pick_model()
    if model:
        try:
            payload = json.dumps({"model": model, "prompt": m.message, "stream": False}).encode("utf-8")
            req = urllib.request.Request(OLLAMA_URL + "/api/generate", data=payload,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
            return {"reply": data.get("response", "").strip() or "(пустой ответ)", "source": "ollama", "model": model}
        except Exception:
            pass
    return {"reply": _local_reply(m.message), "source": "local", "model": None}