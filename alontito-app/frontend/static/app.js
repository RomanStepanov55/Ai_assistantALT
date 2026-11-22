const $ = (id) => document.getElementById(id);
const root = document.documentElement;
const state = { metric:"strength", window:150, refresh:5, grid:true, sound:false, samples:[], tele:null, running:false };
const PALETTE = ["#b070ff","#4cf0ff","#ff5470","#3ce97e","#ffb020","#ff8a3d","#5aa2ff","#f45fff"];

function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function hexRGB(h){ const n=parseInt(h.slice(1),16); return [(n>>16)&255,(n>>8)&255,n&255].join(","); }
function fmtNum(n){ if(n==null)return"—"; n=Number(n); if(!isFinite(n))return"—";
  if(n>=1e12)return(n/1e12).toFixed(2)+"T"; if(n>=1e9)return(n/1e9).toFixed(2)+"B";
  if(n>=1e6)return(n/1e6).toFixed(2)+"M"; if(n>=1e3)return(n/1e3).toFixed(1)+"K"; return String(Math.round(n)); }
function fmtUptime(s){ if(!s)return"—"; const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),x=Math.floor(s%60);
  return (h?h+"ч ":"")+m+"м "+x+"с"; }

let audio=null;
function blip(f){ if(!state.sound)return; try{ audio=audio||new (window.AudioContext||window.webkitAudioContext)();
  const o=audio.createOscillator(),g=audio.createGain(); o.frequency.value=f; o.type="sine";
  g.gain.setValueAtTime(.08,audio.currentTime); g.gain.exponentialRampToValueAtTime(.0001,audio.currentTime+.25);
  o.connect(g).connect(audio.destination); o.start(); o.stop(audio.currentTime+.26);}catch(e){} }

function applyAccent(h){ root.style.setProperty("--accent",h); root.style.setProperty("--accentRGB",hexRGB(h)); }
function applyGlow(v){ root.style.setProperty("--glow",v+"px"); root.style.setProperty("--glowA",(0.15+v/40).toFixed(2)); }

const STAGES=[["Анализ HUD",/анализ|hud|чита/i],["Замер ДО",/замер|до:/i],["Выбор UCB",/ucb|выбира|score/i],
  ["Выполнение",/выполня|подход|train|lift/i],["Замер ПОСЛЕ",/после|after/i],["Рефлексия",/рефлекс|verdict|урок|откат/i]];
function renderStages(logs){ const txt=logs.slice(-14).join(" ");
  $("stages").innerHTML = STAGES.map(([n,re])=>'<span class="stage'+(re.test(txt)?" on":"")+'">'+n+"</span>").join(""); }

function render(status, logs, tele){
  state.running=status.running; state.tele=tele;
  const on=status.running;
  $("status-pill").textContent = on?"● работает":"● остановлен";
  $("status-pill").className = "pill "+(on?"on":"off");
  const badge=$("data-badge"); badge.textContent = on?"LIVE":"АРХИВ"; badge.className="badge "+(on?"live":"archive");
  $("btn-start").disabled=on; $("btn-stop").disabled=!on;
  $("c-state").textContent = on?"ТРЕНИРУЕТСЯ":"ОСТАНОВЛЕН";
  $("c-state").style.color = on?"var(--cyan)":"var(--dim)";
  $("c-pid").textContent = status.pid||"—";
  $("c-uptime").textContent = fmtUptime(status.uptime_s);
  $("c-logs").textContent = logs.length;
  const ts = tele?tele.ts:"";
  $("c-str").textContent = tele?fmtNum(tele.strength):"—";
  $("c-rate").textContent = tele?("+"+fmtNum(tele.rate)):"—";
  $("c-gems").textContent = tele?fmtNum(tele.gems):"—";
  $("c-reb").textContent = tele?tele.rebirths:"—";
  ["ts-str","ts-rate","ts-gems","ts-reb"].forEach(id=>$(id).textContent = ts?("замер: "+ts):"");
  renderStages(logs);
  const c=$("console");
  if(logs.length){ c.innerHTML=logs.map(l=>'<div class="line'+(/\[BOOT\]/.test(l)?" boot":"")+'">'+esc(l)+"</div>").join("");
    c.scrollTop=c.scrollHeight; }
  else c.innerHTML='<div class="empty">Агент остановлен. Нажми «▶ Запустить» — я сам открою Roblox и игру.</div>';
}

function drawChart(){
  const svg=$("chart"); const W=svg.clientWidth||600,H=svg.clientHeight||200,pad=28;
  svg.setAttribute("viewBox","0 0 "+W+" "+H);
  const s=state.samples; const metric=state.metric;
  $("chart-title").textContent = metric==="strength"?"Прогресс силы":metric==="gems"?"Прогресс gems":"Темп /мин";
  if(!s||s.length<2){ svg.innerHTML='<text x="'+pad+'" y="'+(H/2)+'" fill="#8a8a9a" font-size="12">Нет данных телеметрии</text>';
    $("chart-range").textContent=""; return; }
  const vals=s.map(x=>x[metric]); const min=Math.min.apply(null,vals),max=Math.max.apply(null,vals);
  const span=(max-min)||1;
  $("chart-range").textContent = s[0].ts+" → "+s[s.length-1].ts+" · "+s.length+" замеров";
  let grid="";
  if(state.grid){ for(let i=1;i<4;i++){ const y=pad+(H-2*pad)*i/4;
    grid+='<line x1="'+pad+'" y1="'+y+'" x2="'+(W-10)+'" y2="'+y+'" stroke="rgba(176,112,255,.12)" stroke-width="1"/>'; } }
  const pts=s.map((x,i)=>{ const px=pad+i*(W-pad-10)/(s.length-1);
    const py=H-18-(x[metric]-min)*(H-40)/span; return [px,py]; });
  const path=pts.map((p,i)=>(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)).join(" ");
  const area=path+" L "+pts[pts.length-1][0].toFixed(1)+" "+(H-18)+" L "+pts[0][0].toFixed(1)+" "+(H-18)+" Z";
  svg.innerHTML='<defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">'+
    '<stop offset="0" stop-color="rgba(76,240,255,.30)"/><stop offset="1" stop-color="rgba(76,240,255,0)"/></linearGradient></defs>'+
    grid+'<path d="'+area+'" fill="url(#cg)"/>'+
    '<path d="'+path+'" fill="none" stroke="#4cf0ff" stroke-width="2" style="filter:drop-shadow(0 0 5px rgba(76,240,255,.7))"/>'+
    '<text x="'+pad+'" y="12" fill="#8a8a9a" font-size="10">max '+fmtNum(max)+'</text>'+
    '<text x="'+pad+'" y="'+(H-4)+'" fill="#8a8a9a" font-size="10">min '+fmtNum(min)+'</text>';
}
function exportPNG(){ const svg=$("chart"); const xml=new XMLSerializer().serializeToString(svg);
  const img=new Image(); const W=svg.clientWidth||600,H=svg.clientHeight||200;
  img.onload=function(){ const cv=document.createElement("canvas"); cv.width=W*2; cv.height=H*2;
    const cx=cv.getContext("2d"); cx.fillStyle="#0a0a0f"; cx.fillRect(0,0,cv.width,cv.height);
    cx.drawImage(img,0,0,cv.width,cv.height);
    const a=document.createElement("a"); a.download="alontito-chart.png"; a.href=cv.toDataURL("image/png"); a.click(); };
  img.src="data:image/svg+xml;base64,"+btoa(unescape(encodeURIComponent(xml))); }

function renderLearning(d){ const el=$("learning"); let html="";
  const caps=d.capabilities||[];
  if(caps.length){ const max=Math.max.apply(null,caps.map(c=>c.score).concat([1]));
    html+='<div class="learn-sub">UCB-рейтинг решений</div>';
    caps.slice(0,6).forEach(c=>{ const w=Math.max(3,Math.round(c.score/max*100));
      html+='<div class="ucb-row" title="'+esc(c.description)+'"><div class="ucb-name">'+esc(c.name)+(c.dynamic?" ✦":"")+'</div>'+
        '<div class="ucb-bar"><div class="ucb-fill" style="width:'+w+'%"></div></div>'+
        '<div class="ucb-val">'+c.score+" · "+c.attempts+"x</div></div>"; }); }
  const sd=d.selfdev||{};
  if(sd.active){ html+='<div class="learn-sub">Открытый эксперимент</div><div class="lessons"><li>'+esc(sd.active.change||"—")+
    " (замеров "+(sd.active.tries||0)+", вердикт "+esc(sd.active.last_verdict||"—")+")</li></div>"; }
  const lessons=(sd.lessons&&sd.lessons.length)?sd.lessons:(d.insights||[]);
  if(lessons.length){ html+='<div class="learn-sub">Уроки агента</div><ul class="lessons">'+
    lessons.slice(-5).map(l=>"<li>"+esc(l)+"</li>").join("")+"</ul>"; }
  el.innerHTML=html||'<div class="dim">Данных обучения пока нет.</div>'; }

let pollTimer=null;
async function pollChart(){ try{ const r=await fetch("/api/telemetry/samples?limit="+state.window);
  const d=await r.json(); state.samples=d.samples; drawChart(); }catch(e){} }
async function pollLearning(){ try{ const r=await fetch("/api/learning"); renderLearning(await r.json()); }catch(e){} }
function restartPoll(){ if(pollTimer)clearInterval(pollTimer); pollTimer=setInterval(pollChart,state.refresh*1000); }

function connect(){ const ws=new WebSocket("ws://"+location.host+"/ws/agent");
  ws.onmessage=e=>{ const d=JSON.parse(e.data); render(d.status,d.logs,d.tele); };
  ws.onclose=()=>setTimeout(connect,2000); ws.onerror=()=>ws.close(); }

const chatLog=[];
function pushMsg(role,text,src){ chatLog.push({role,text,src});
  const el=document.createElement("div"); el.className="msg "+role;
  el.innerHTML=esc(text)+(src?'<span class="src">'+esc(src)+"</span>":"");
  $("chat-log").appendChild(el); $("chat-log").scrollTop=$("chat-log").scrollHeight; }
async function sendChat(){ const t=$("chat-text").value.trim(); if(!t)return;
  $("chat-text").value=""; pushMsg("user",t); blip(660);
  try{ const r=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:t})});
    const d=await r.json(); pushMsg("bot",d.reply,(d.source==="ollama"?"Ollama · "+d.model:"локальный ответчик"));
    $("chat-source").textContent="источник: "+(d.source==="ollama"?"Ollama":"local"); blip(440);
  }catch(e){ pushMsg("bot","Бэкенд недоступен.","local"); } }

async function loadSafety(){ try{ const r=await fetch("/api/safety"); const d=await r.json();
  if(d.error){ $("safety").innerHTML='<div class="dim">'+esc(d.error)+"</div>"; return; }
  $("safety").innerHTML='<div class="learn-sub">Золотое правило</div><div class="lessons"><li>'+esc(d.golden_rule)+"</li></div>"+
    '<div class="learn-sub">Разрешённые клики</div><ul class="lessons">'+d.allowed_click_rois.map(x=>"<li>"+esc(x)+"</li>").join("")+"</ul>"+
    '<div class="learn-sub">Пол гемов / траты</div><ul class="lessons"><li>floor: '+fmtNum(d.gems_safety_floor)+
    "</li><li>whitelist: "+esc(d.spend_whitelist.join(", "))+"</li></ul>"; }catch(e){} }
async function loadKnowledge(){ try{ const r=await fetch("/api/knowledge"); const d=await r.json();
  $("knowledge").innerHTML = d.ok?('<div class="learn-sub">Уроков в общей базе</div><div class="card-value">'+d.lessons+
    '</div><div class="dim">'+esc(d.source)+"</div>"):'<div class="dim">'+esc(d.error||"недоступно")+"</div>"; }catch(e){} }

function bindRail(){
  $("r-accent").oninput=e=>applyAccent(e.target.value);
  $("r-glow").oninput=e=>applyGlow(+e.target.value);
  $("r-metric").onchange=e=>{ state.metric=e.target.value; drawChart(); };
  $("r-window").oninput=e=>{ state.window=+e.target.value; pollChart(); };
  $("r-refresh").oninput=e=>{ state.refresh=+e.target.value; restartPoll(); };
  $("r-grid").onchange=e=>{ state.grid=e.target.checked; drawChart(); };
  $("r-compact").onchange=e=>document.body.classList.toggle("compact",e.target.checked);
  $("r-sound").onchange=e=>{ state.sound=e.target.checked; if(state.sound)blip(520); };
  $("r-reset").onclick=()=>{ applyAccent("#b070ff"); applyGlow(14); $("r-accent").value="#b070ff"; $("r-glow").value=14;
    state.metric="strength"; $("r-metric").value="strength"; state.window=150; $("r-window").value=150;
    state.refresh=5; $("r-refresh").value=5; state.grid=true; $("r-grid").checked=true;
    document.body.classList.remove("compact"); $("r-compact").checked=false; restartPoll(); drawChart(); };
  $("r-rand").onclick=()=>{ const h=PALETTE[Math.floor(Math.random()*PALETTE.length)];
    applyAccent(h); $("r-accent").value=h; const g=8+Math.floor(Math.random()*22); applyGlow(g); $("r-glow").value=g; blip(700); };
  $("r-export").onclick=exportPNG;
}
function bindNav(){ document.querySelectorAll(".nav-btn").forEach(b=>{ b.onclick=()=>{
  document.querySelectorAll(".nav-btn").forEach(x=>x.classList.remove("active")); b.classList.add("active");
  const v=b.dataset.view;
  ["dashboard","chat","settings"].forEach(k=>$("view-"+k).classList.toggle("active",k===v));
  if(v==="settings"){ loadSafety(); loadKnowledge(); }
  if(v==="dashboard"){ drawChart(); }
}; }); }

$("btn-start").onclick=async()=>{ $("btn-start").disabled=true; blip(660);
  try{ await fetch("/api/agent/start",{method:"POST"}); }catch(e){} };
$("btn-stop").onclick=async()=>{ $("btn-stop").disabled=true; blip(330);
  try{ await fetch("/api/agent/stop",{method:"POST"}); }catch(e){} };
$("chat-send").onclick=sendChat;
$("chat-text").addEventListener("keydown",e=>{ if(e.key==="Enter")sendChat(); });
$("kn-sync").onclick=loadKnowledge;
window.addEventListener("resize",drawChart);

applyAccent("#b070ff"); applyGlow(14);
bindRail(); bindNav(); connect(); pollChart(); pollLearning(); restartPoll();
pushMsg("bot","Привет! Я Алонтито. Спроси про статус, силу, уроки или запуск — или включи Ollama для полноценного диалога.","local");
// --- Phase 1.5: target override + engine status ---
function loadTarget(){ try{ const s=JSON.parse(localStorage.getItem("alontito_target")||"{}");
  $("t-title").value=s.title||""; $("t-proc").value=s.process||""; }catch(e){} }
function saveTarget(){ localStorage.setItem("alontito_target",JSON.stringify({title:$("t-title").value,process:$("t-proc").value}));
  $("t-result").textContent="Сохранено. Будет использовано при запуске."; }
async function findTarget(){ const q=new URLSearchParams(); if($("t-title").value)q.set("title",$("t-title").value);
  if($("t-proc").value)q.set("process",$("t-proc").value);
  try{ const r=await fetch("/api/agent/discover?"+q.toString()); const d=await r.json();
    $("t-result").innerHTML = d.target? ("Найдено: "+esc(d.target.title||"?")+" · pid "+d.target.pid+" · "+esc(d.target.exe)) : "Окно не найдено. Запусти игру или укажи название выше.";
  }catch(e){ $("t-result").textContent="Ошибка запроса."; } }
async function loadEngine(){ try{ const r=await fetch("/api/engine"); const d=await r.json();
  $("engine").innerHTML = d.connected? ('<div class="learn-sub">Подключено</div><ul class="lessons">'+
    d.models.map(m=>"<li>"+esc(m)+"</li>").join("")+"</ul>") : '<div class="dim">'+esc(d.note)+"</div>";
  }catch(e){} }
loadTarget();
$("t-save").onclick=saveTarget;
$("t-find").onclick=findTarget;
$("e-check").onclick=loadEngine;
$("btn-start").onclick=async()=>{ $("btn-start").disabled=true; blip(660);
  const s=JSON.parse(localStorage.getItem("alontito_target")||"{}");
  try{ await fetch("/api/agent/start",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({title:s.title||null,process:s.process||null})}); }catch(e){} };