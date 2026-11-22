import json
import asyncio
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
import uvicorn

from brain.opus_local_core import OpusLocalCore
from games.universal_explorer import UniversalExplorer


HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>Alontito Opus 5 — Центр управления</title>
  <style>
    body { background: #0d0f17; color: #e2e8f0; font-family: 'Segoe UI', sans-serif; margin: 0; padding: 20px; }
    .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; max-width: 1200px; margin: auto; }
    .card { background: #161a29; border: 1px solid #2d3552; border-radius: 10px; padding: 16px; }
    h2 { margin-top: 0; color: #4cf0ff; font-size: 18px; }
    #stream { height: 320px; overflow-y: auto; background: #0a0c12; padding: 12px; border-radius: 8px;
              font-family: Consolas, monospace; font-size: 13px; white-space: pre-wrap; color: #b070ff; }
    .btn { background: #4cf0ff; color: #090d16; border: none; padding: 8px 14px; border-radius: 6px;
           font-weight: bold; cursor: pointer; margin-right: 8px; }
    .btn-danger { background: #ff5c77; color: #fff; }
    input { width: 72%; padding: 8px; border-radius: 6px; border: 1px solid #2d3552; background: #0a0c12; color: #fff; }
  </style>
</head>
<body>
  <div class="grid">
    <div class="card">
      <h2>🧠 Поток мыслей &lt;thinking&gt; в реальном времени</h2>
      <div id="stream">Ожидание рассуждений Алонтито...\n</div>
      <div style="margin-top: 12px;">
        <input id="prompt" placeholder="Задай задачу Алонтито..." />
        <button class="btn" onclick="sendPrompt()">Отправить</button>
      </div>
    </div>
    <div class="card">
      <h2>📊 Телеметрия UCB1 &amp; Обучение</h2>
      <div id="stats">Загрузка метрик...</div>
      <hr style="border-color:#2d3552; margin: 14px 0;">
      <button class="btn" onclick="ctrl('/api/explore/start')">▶ Старт UCB1</button>
      <button class="btn btn-danger" onclick="ctrl('/api/explore/stop')">⏹ Стоп</button>
    </div>
  </div>
<script>
  const streamBox = document.getElementById('stream');
  const es = new EventSource('/api/events');
  es.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'thinking_chunk') {
      streamBox.textContent += ev.data.text;
      streamBox.scrollTop = streamBox.scrollHeight;
    } else if (ev.type === 'ucb1_cycle') {
      refreshStats();
    }
  };
  async function sendPrompt() {
    const inp = document.getElementById('prompt');
    if (!inp.value) return;
    streamBox.textContent += "\n\n>>> " + inp.value + "\n";
    const q = inp.value; inp.value = '';
    await fetch('/api/chat', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({query: q}) });
  }
  async function ctrl(url) { await fetch(url, {method: 'POST'}); refreshStats(); }
  async function refreshStats() {
    const r = await fetch('/api/stats');
    const d = await r.json();
    document.getElementById('stats').innerHTML =
      `<p>Всего попыток UCB1: <b>${d.total_attempts}</b></p>` +
      `<p>Примеров в датасете LoRA: <b>${d.dataset_size}</b></p>` +
      `<p>Авто-инструментов: <b>${d.tools_count}</b></p>`;
  }
  refreshStats();
</script>
</body>
</html>
"""


def create_dashboard_app(brain: OpusLocalCore, explorer: UniversalExplorer) -> FastAPI:
    app = FastAPI(title="Alontito Omni Dashboard")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTML_PAGE

    @app.get("/api/stats")
    async def stats() -> Dict[str, Any]:
        ds_path = Path(brain.cfg.dataset_path)
        ds_lines = len(ds_path.read_text(encoding="utf-8").splitlines()) if ds_path.exists() else 0
        return {
            "total_attempts": explorer.total_attempts,
            "explorer_running": explorer.running,
            "dataset_size": ds_lines,
            "tools_count": len(brain.registered_tools),
            "arms": {k: {"attempts": v.attempts, "rate": round(v.exploit_rate, 2)} for k, v in explorer.arms.items()}
        }

    @app.post("/api/chat")
    async def chat(req: Request):
        body = await req.json()
        query = body.get("query", "")
        res = await asyncio.to_thread(brain.think_and_act, query)
        return res

    @app.post("/api/explore/start")
    async def start_explore():
        explorer.start_background()
        return {"status": "started"}

    @app.post("/api/explore/stop")
    async def stop_explore():
        explorer.stop()
        return {"status": "stopped"}

    @app.get("/api/events")
    async def sse_events():
        q = brain.bus.subscribe()

        async def event_generator():
            try:
                while True:
                    try:
                        item = q.get_nowait()
                        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                    except Exception:
                        await asyncio.sleep(0.15)
            finally:
                brain.bus.unsubscribe(q)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return app


def run_dashboard_server(brain: OpusLocalCore, explorer: UniversalExplorer, port: int = 8080) -> None:
    app = create_dashboard_app(brain, explorer)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
