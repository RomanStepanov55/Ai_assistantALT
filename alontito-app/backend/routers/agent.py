"""REST-эндпоинты управления агентом."""
from fastapi import APIRouter
from backend.services.agent_bridge import bridge

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/status")
def status():
    return bridge.status()


@router.post("/start")
def start():
    return bridge.start()


@router.post("/stop")
def stop():
    return bridge.stop()


@router.get("/logs")
def logs(limit: int = 100):
    return {"lines": bridge.logs(limit)}