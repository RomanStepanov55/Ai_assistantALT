const $ = (id) => document.getElementById(id);
const pill = $("status-pill"), btnStart = $("btn-start"), btnStop = $("btn-stop");
const consoleEl = $("console");

function esc(s){ return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function fmtUptime(s){
  if(!s) return "—";
  const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=Math.floor(s%60);
  return (h?h+"ч ":"")+m+"м "+sec+"с";
}
function render(status, logs){
  const on = status.running;
  pill.textContent = on ? "● работает" : "● остановлен";
  pill.className = "pill " + (on?"on":"off");
  btnStart.disabled = on; btnStop.disabled = !on;
  $("c-state").textContent = on ? "ТРЕНИРУЕТСЯ" : "ОСТАНОВЛЕН";
  $("c-state").style.color = on ? "var(--cyan)" : "var(--dim)";
  $("c-pid").textContent = status.pid || "—";
  $("c-uptime").textContent = fmtUptime(status.uptime_s);
  $("c-logs").textContent = logs.length;
  if(logs.length){
    consoleEl.innerHTML = logs.map(l=>'<div class="line">'+esc(l)+'</div>').join("");
    consoleEl.scrollTop = consoleEl.scrollHeight;
  } else {
    consoleEl.innerHTML = '<div class="empty">Агент остановлен. Нажми «▶ Запустить».</div>';
  }
}
function connect(){
  const ws = new WebSocket("ws://"+location.host+"/ws/agent");
  ws.onmessage = (e)=>{ const d=JSON.parse(e.data); render(d.status, d.logs); };
  ws.onclose = ()=>setTimeout(connect, 2000);
  ws.onerror = ()=>ws.close();
}
connect();
btnStart.onclick = async ()=>{ btnStart.disabled=true; try{ await fetch("/api/agent/start",{method:"POST"}); }catch(e){} };
btnStop.onclick  = async ()=>{ btnStop.disabled=true;  try{ await fetch("/api/agent/stop",{method:"POST"});  }catch(e){} };
document.querySelectorAll(".nav-btn").forEach(b=>{
  b.onclick = ()=>{
    document.querySelectorAll(".nav-btn").forEach(x=>x.classList.remove("active"));
    b.classList.add("active");
    const v = b.dataset.view;
    $("view-dashboard").classList.toggle("active", v==="dashboard");
    $("view-soon").classList.toggle("active", v!=="dashboard");
  };
});