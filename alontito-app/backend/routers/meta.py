"""Метаданные: безопасность проекта и синхронизация базы знаний (чтение)."""
from __future__ import annotations
import json
import sys
import urllib.request

from fastapi import APIRouter

from backend.config import PROJECT_ROOT

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/safety")
def safety():
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        import config as pc  # noqa: E402
        return {
            "allowed_click_rois": sorted(pc.ALLOWED_CLICK_ROIS),
            "gems_safety_floor": pc.GEMS_SAFETY_FLOOR,
            "spend_whitelist": sorted(pc.SPEND_WHITELIST),
            "max_spend_per_action": pc.MAX_SPEND_PER_ACTION,
            "golden_rule": "Никогда не покупать за реальные деньги; никаких инъекций и трейдов.",
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/knowledge")
def knowledge():
    url = "https://raw.githubusercontent.com/alontito/alontito-knowledge/main/knowledge/lessons.jsonl"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            lines = [l for l in r.read().decode("utf-8").splitlines() if l.strip()]
        return {"ok": True, "lessons": len(lines), "source": url}
    except Exception as e:
        return {"ok": False, "error": str(e)}