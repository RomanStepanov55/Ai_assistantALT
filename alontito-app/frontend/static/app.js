const $ = (id) => document.getElementById(id);
const pill = $("status-pill"), btnStart = $("btn-start"), btnStop = $("btn-stop");
const consoleEl = $("console");

function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function fmtNum(n){
  if(n==null) return "—";
  n = Number(n);
  if(!isFinite(n)) return "—";
  if(n>=1e12) return (n/1e12).toFixed(2)+"T";
  if(n>=1e9) return (n/1e9).toFixed(2)+"B";
  if(n>=1e6) return (n/1e6).toFixed(2)+"M";
  if(n>=1e3) return (n/1e3).toFixed(1)+"K";
  return String(Math.round(n));
}
function fmtUptime(s){
  if(!s) return "—";
  const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=Math.floor(s%60);
  return (h?h+"ч ":"")+m+"м "+sec+"с";
}
function render(status, logs, tele){
  const on = status.running;
  pill.textContent = on ? "● работает" : "● остановлен";
  pill.className = "pill " + (on?"on":"off");
  btnStart.disabled = on; btnStop.disabled = !on;
  $("c-state").textContent = on ? "ТРЕНИРУЕТСЯ" : "ОСТАНОВЛЕН";
  $("c-state").style.color = on ? "var(--cyan)" : "var(--dim)";
  $("c-pid").textContent = status.pid || "—";
  $("c-uptime").textContent = fmtUptime(status.uptime_s);
  $("c-logs").textContent = logs.length;
  $("c-str").textContent = tele ? fmtNum(tele.strength) : "—";
  $("c-rate").textContent = tele ? ("+"+fmtNum(tele.rate)) : "—";
  $("c-gems").textContent = tele ? fmtNum(tele.gems) : "—";
  $("c-reb").textContent = tele ? tele.rebirths : "—";
  if(logs.length){
    consoleEl.innerHTML = logs.map(l=>'<div class="line">'+esc(l)+'</div>').join("");
    consoleEl.scrollTop = consoleEl.scrollHeight;
  } else {
    consoleEl.innerHTML = '<div class="empty">Агент остановлен. Нажми «▶ Запустить».</div>';
  }
}
function drawChart(samples){
  const svg = $("chart");
  const W = svg.clientWidth || 600, H = 190, pad = 26;
  svg.setAttribute("viewBox", "0 0 "+W+" "+H);
  if(!samples || samples.length < 2){
    svg.innerHTML = '<text x="'+pad+'" y="'+(H/2)+'" fill="#8a8a9a" font-size="12">Нет данных телеметрии</text>';
    return;
  }
  const vals = samples.map(s=>s.strength);
  const min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
  const span = (max-min)||1;
  const pts = samples.map((s,i)=>{
    const x = pad + i*(W-pad-10)/(samples.length-1);
    const y = H-18 - (s.strength-min)*(H-40)/span;
    return [x,y];
  });
  const path = pts.map((p,i)=>(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)).join(" ");
  const area = path+" L "+pts[pts.length-1][0].toFixed(1)+" "+(H-18)+" L "+pts[0][0].toFixed(1)+" "+(H-18)+" Z";
  svg.innerHTML =
    '<defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">'+
    '<stop offset="0" stop-color="rgba(76,240,255,.30)"/>'+
    '<stop offset="1" stop-color="rgba(76,240,255,0)"/></linearGradient></defs>'+
    '<path d="'+area+'" fill="url(#cg)"/>'+
    '<path d="'+path+'" fill="none" stroke="#4cf0ff" stroke-width="2" style="filter:drop-shadow(0 0 5px rgba(76,240,255,.7))"/>'+
    '<text x="'+pad+'" y="12" fill="#8a8a9a" font-size="10">max '+fmtNum(max)+'</text>'+
    '<text x="'+pad+'" y="'+(H-4)+'" fill="#8a8a9a" font-size="10">min '+fmtNum(min)+' · '+samples.length+' замеров</text>';
}
function renderLearning(d){
  const el = $("learning");
  let html = "";
  const caps = d.capabilities||[];
  if(caps.length){
    const max = Math.max.apply(null, caps.map(c=>c.score).concat([1]));
    html += '<div class="learn-sub">UCB-рейтинг решений</div>';
    caps.slice(0,6).forEach(c=>{
      const w = Math.max(3, Math.round(c.score/max*100));
      html += '<div class="ucb-row" title="'+esc(c.description)+'">'+
        '<div class="ucb-name">'+esc(c.name)+(c.dynamic?' ✦':'')+'</div>'+
        '<div class="ucb-bar"><div class="ucb-fill" style="width:'+w+'%"></div></div>'+
        '<div class="ucb-val">'+c.score+' · '+c.attempts+'x</div></div>';
    });
  }
  const sd = d.selfdev||{};
  if(sd.active){
    html += '<div class="learn-sub">Открытый эксперимент</div>'+
      '<div class="exp">'+esc(sd.active.change||"—")+
      '<br><span class="dim">замеров: '+(sd.active.tries||0)+' · вердикт: '+esc(sd.active.last_verdict||"—")+'</span></div>';
  }
  if(sd.params && Object.keys(sd.params).length){
    html += '<div class="chips">'+Object.keys(sd.params).map(k=>'<span class="chip">'+esc(k)+': '+esc(sd.params[k])+'</span>').join("")+'</div>';
  }
  const lessons = (sd.lessons && sd.lessons.length) ? sd.lessons : (d.insights||[]);
  if(lessons.length){
    html += '<div class="learn-sub">Уроки агента</div><ul class="lessons">'+
      lessons.slice(-5).map(l=>'<li>'+esc(l)+'</li>').join("")+'</ul>';
  }
  el.innerHTML = html || '<div class="dim">Данных обучения пока нет.</div>';
}
async function pollChart(){ try{ const r=await fetch("/api/telemetry/samples?limit=150"); const d=await r.json(); drawChart(d.samples); }catch(e){} }
async function pollLearning(){ try{ const r=await fetch("/api/learning"); const d=await r.json(); renderLearning(d); }catch(e){} }
function connect(){
  const ws = new WebSocket("ws://"+location.host+"/ws/agent");
  ws.onmessage = (e)=>{ const d=JSON.parse(e.data); render(d.status, d.logs, d.tele); };
  ws.onclose = ()=>setTimeout(connect, 2000);
  ws.onerror = ()=>ws.close();
}
connect();
pollChart(); pollLearning();
setInterval(pollChart, 5000);
setInterval(pollLearning, 10000);
window.addEventListener("resize", pollChart);
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