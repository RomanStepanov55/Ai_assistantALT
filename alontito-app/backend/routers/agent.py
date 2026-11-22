"""REST управления агентом + поиск цели Roblox."""
from fastapi import APIRouter
from pydantic import BaseModel

from backend.services.agent_bridge import bridge
from backend.services import roblox_finder as rf

router = APIRouter(prefix="/api/agent", tags=["agent"])


class StartReq(BaseModel):
    title: str | None = None
    process: str | None = None


@router.get("/status")
def status():
    return bridge.status()


@router.post("/start")
def start(req: StartReq | None = None):
    t = req.title if req else None
    p = req.process if req else None
    return bridge.start(t, p)


@router.post("/stop")
def stop():
    return bridge.stop()


@router.get("/logs")
def logs(limit: int = 100):
    return {"lines": bridge.logs(limit)}


@router.get("/discover")
def discover(title: str | None = None, process: str | None = None):
    return {"target": rf.find_target(title, process)}