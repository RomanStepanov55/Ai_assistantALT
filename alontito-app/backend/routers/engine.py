"""Статус локального языкового движка (Ollama)."""
import json
import urllib.request

from fastapi import APIRouter

from backend.config import OLLAMA_URL

router = APIRouter(prefix="/api", tags=["engine"])


@router.get("/engine")
def engine():
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=2) as r:
            tags = json.loads(r.read().decode("utf-8"))
        models = [m.get("name", "") for m in tags.get("models", [])]
        return {"connected": True, "models": models,
                "note": "Движок локальный. Для 8 ГБ RAM рекомендуется qwen2.5:3b-instruct."}
    except Exception:
        return {"connected": False, "models": [],
                "note": "Ollama не запущена. Чат работает на встроенном ответчике Алонтито. "
                        "Модели качай через VPN (провайдер блокирует registry.ollama.ai)."}