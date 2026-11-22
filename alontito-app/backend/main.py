"""Alontito Control Center — FastAPI бэкенд (Фаза 1.3)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.config import HOST, PORT, APP_DIR
from backend.routers.agent import router as agent_router
from backend.routers.telemetry import router as telemetry_router
from backend.services.agent_bridge import bridge
from backend.services import telemetry_reader as tr

app = FastAPI(title="Alontito Control Center", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)
app.include_router(telemetry_router)

FRONTEND_DIR = APP_DIR / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def root():
    return (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "alontito-backend", "version": "0.3.0"}


@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json({
                "status": bridge.status(),
                "logs": bridge.logs(30),
                "tele": tr.last_sample(),
            })
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)