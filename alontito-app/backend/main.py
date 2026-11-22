"""Alontito Control Center — FastAPI бэкенд (Фаза 1.1)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from backend.config import HOST, PORT
from backend.routers.agent import router as agent_router
from backend.services.agent_bridge import bridge

app = FastAPI(title="Alontito Control Center", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:1420", "http://127.0.0.1:1420",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)

ROOT_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Alontito Control Center</title>
<style>
  body { background:#0a0a0f; color:#e0e0e0; font-family:Consolas,monospace;
         display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }
  .card { background:rgba(26,26,46,.6); border:1px solid rgba(176,112,255,.35);
          border-radius:16px; padding:40px 48px; box-shadow:0 0 40px rgba(176,112,255,.15); text-align:center; }
  h1 { color:#b070ff; font-size:22px; letter-spacing:3px; margin:0 0 8px;
       text-shadow:0 0 12px rgba(176,112,255,.6); }
  .sub { color:#888; font-size:12px; margin-bottom:24px; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:8px; }
  .on { background:#4cf0ff; box-shadow:0 0 10px #4cf0ff; }
  .off { background:#555; }
  #status { font-size:14px; margin-bottom:24px; }
  a { color:#4cf0ff; text-decoration:none; margin:0 12px; font-size:13px; }
  a:hover { text-shadow:0 0 8px rgba(76,240,255,.8); }
</style>
</head>
<body>
<div class="card">
  <h1>ALONTITO</h1>
  <div class="sub">CONTROL CENTER &middot; backend v0.1.0</div>
  <div id="status"><span class="dot off"></span>проверка...</div>
  <div>
    <a href="/docs">API Docs</a>
    <a href="/api/health">Health</a>
    <a href="/api/agent/status">Agent Status</a>
  </div>
</div>
<script>
async function tick() {
  try {
    const r = await fetch('/api/agent/status');
    const s = await r.json();
    const el = document.getElementById('status');
    el.innerHTML = s.running
      ? '<span class="dot on"></span>АГЕНТ РАБОТАЕТ &middot; pid ' + s.pid + ' &middot; ' + s.uptime_s + 's'
      : '<span class="dot off"></span>агент остановлен';
  } catch(e) {}
}
tick(); setInterval(tick, 2000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def root():
    return ROOT_PAGE


@app.get("/api/health")
def health():
    return {"ok": True, "service": "alontito-backend", "version": "0.1.0"}


@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json({"status": bridge.status(), "logs": bridge.logs(30)})
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)