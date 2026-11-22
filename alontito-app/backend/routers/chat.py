"""Чат с Алонтито: всегда отвечает локально; Ollama лишь расширяет свободную речь."""
from __future__ import annotations
import json
import urllib.request

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config import OLLAMA_URL
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
    learn = tr.learning()
    if any(k in t for k in ("привет", "здаров", "hello")):
        return "Привет! Я Алонтито — твой локальный агент. Я всегда на связи даже без Ollama. Спроси про статус, силу, уроки или запуск."
    if any(k in t for k in ("статус", "состоян", "как ты")):
        s = "тренируюсь" if st["running"] else "отдыхаю"
        extra = f" Последний замер: сила {last['strength']:,} ({last['ts']})." if last else ""
        return f"Сейчас я {s}.{extra}"
    if any(k in t for k in ("сила", "прогресс", "темп")):
        if last:
            return f"По архиву: сила {last['strength']:,}, темп +{last['rate']}/мин, ребёрнов {last['rebirths']} (замер {last['ts']}). Запусти меня — соберу свежие данные."
        return "Замеров пока нет. Нажми «Запустить» — я открою игру и начну собирать телеметрию."
    if any(k in t for k in ("урок", "научил", "памят", "знаешь")):
        ins = learn.get("insights") or []
        return "Вот что я усвоил: " + ("; ".join(ins[-3:]) if ins else "пока мало уроков, но я учусь на каждом замере.")
    if any(k in t for k in ("запуст", "старт", "игра", "роблокс")):
        return "Кнопка «Запустить» сама найдёт или откроет Roblox (даже переименованный) и начнёт цикл. Следи за «Поток агента»."
    if any(k in t for k in ("кто ты", "оллама", "ollama", "ии")):
        return ("Я Алонтито — твой собственный агент: моя память, навыки и решения. Ollama — лишь локальный движок речи; "
                "без неё я отвечаю встроенным ответчиком, с ней — свободнее. Ничего не уходит в облако.")
    return ("Я Алонтито, на связи локально. Могу рассказать про статус, силу, уроки и запуск. "
            "Для свободной беседы включи Ollama — но я остаюсь твоим агентом, а не чужим.")


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