/* Алонтито — приложение агента. Один файл, без сборки и зависимостей.
   Всё, что показывает экран, приходит из API; числа без данных — тире,
   не ноль. Токен хранится в localStorage и убирается из адреса. */
"use strict";

/* ------------------------------------------------------------- helpers */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s)
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");
const dash = (v) => (v === null || v === undefined || v === "") ? "—" : v;
const fmtTime = (ts) => {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleDateString("ru", { day: "2-digit", month: "2-digit" }) +
    " " + d.toLocaleTimeString("ru", { hour: "2-digit", minute: "2-digit" });
};

async function api(path, opts = {}) {
  const headers = Object.assign({ "X-Pult-Token": localStorage.getItem("pult_token") || "" }, opts.headers || {});
  if (opts.json) {
    headers["Content-Type"] = "application/json";
    opts = Object.assign({}, opts, { body: JSON.stringify(opts.json), headers });
  } else {
    opts = Object.assign({}, opts, { headers });
  }
  const resp = await fetch(path, opts);
  if (resp.status === 401) { showLogin("Сессия недействительна"); throw new Error("401"); }
  const text = await resp.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { raw: text }; }
  if (!resp.ok) throw Object.assign(new Error(data.error || data.reason || resp.statusText), { data, status: resp.status });
  return data;
}

/* --------------------------------------------------------------- token */

let isMaster = false;

function showLogin(note) {
  $("login-error").textContent = note || "";
  $("login").classList.remove("hidden");
  $("token-input").focus();
}

function hideLogin() { $("login").classList.add("hidden"); }

async function tryToken(token, deviceName) {
  // 6 цифр без дефисов — это код сопряжения: обменяем на токен устройства.
  if (/^\d{6}$/.test(token)) {
    const paired = await fetch("/api/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: token, name: deviceName || "устройство" }),
    });
    const data = await paired.json();
    if (!paired.ok) throw new Error(data.reason || "код не принят");
    token = data.token;
    localStorage.setItem("pult_device", data.name || "устройство");
  }
  localStorage.setItem("pult_token", token);
  const probe = await fetch("/api/state", { headers: { "X-Pult-Token": token } });
  if (probe.status === 401) { localStorage.removeItem("pult_token"); throw new Error("токен не принят сервером"); }
  const url = new URL(location.href);
  if (url.searchParams.has("token")) { url.searchParams.delete("token"); history.replaceState(null, "", url.pathname); }
  hideLogin();
  boot();
}

$("token-save").addEventListener("click", () => {
  $("login-error").textContent = "";
  tryToken($("token-input").value.trim(), $("device-name").value.trim())
    .catch((e) => { $("login-error").textContent = e.message; });
});
$("token-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("token-save").click(); });

/* --------------------------------------------------------------- theme */

const THEMES = ["system", "light", "dark"];
const THEME_LABEL = { system: "Тема: как в системе", light: "Тема: светлая", dark: "Тема: тёмная" };
const THEME_ICON = {
  system: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5"/><path d="M8 2.5v11M8 2.5a5.5 5.5 0 0 1 0 11"/></svg>',
  light: '<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="3.2"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.5 3.5l1.1 1.1M11.4 11.4l1.1 1.1M12.5 3.5l-1.1 1.1M4.6 11.4l-1.1 1.1"/></svg>',
  dark: '<svg viewBox="0 0 16 16"><path d="M13 9.5A6 6 0 0 1 6.5 3 6 6 0 1 0 13 9.5z"/></svg>',
};
function applyTheme(mode) {
  const dark = mode === "dark" || (mode === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  $("theme-label").textContent = THEME_LABEL[mode];
  $("theme-icon").innerHTML = THEME_ICON[mode];
}
$("btn-theme").addEventListener("click", () => {
  const cur = localStorage.getItem("pult_theme") || "system";
  const next = THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
  localStorage.setItem("pult_theme", next);
  applyTheme(next);
});
applyTheme(localStorage.getItem("pult_theme") || "system");

/* ---------------------------------------------------------- navigation */

function openTab(name) {
  document.querySelectorAll(".tab").forEach((t) => {
    const on = t.dataset.tab === name;
    t.classList.toggle("active", on);
    if (on) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
  });
  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  const page = $("tab-" + name);
  if (page) page.classList.add("active");
  closeMenu();
  if (name === "memory") loadMemory();
  if (name === "automations") loadAutomations();
  if (name === "inbox") loadInbox();
  if (name === "journal") loadJournal();
  if (name === "settings") loadSettings();
}
document.querySelectorAll(".tab").forEach((t) =>
  t.addEventListener("click", () => openTab(t.dataset.tab)));

const isMobile = () => matchMedia("(max-width: 900px)").matches;
function closeMenu() { $("sidebar").classList.remove("open"); $("btn-menu").setAttribute("aria-expanded", "false"); }
$("btn-menu").addEventListener("click", () => {
  const open = !$("sidebar").classList.contains("open");
  $("sidebar").classList.toggle("open", open);
  $("btn-menu").setAttribute("aria-expanded", String(open));
});
$("btn-menu-close").addEventListener("click", closeMenu);
document.addEventListener("click", (e) => {
  if (isMobile() && $("sidebar").classList.contains("open") &&
      !$("sidebar").contains(e.target) && !$("btn-menu").contains(e.target)) closeMenu();
});

/* ------------------------------------------------------------ conn/SSE */

let sse = null;
function connectSSE() {
  if (sse) sse.close();
  const token = localStorage.getItem("pult_token") || "";
  sse = new EventSource("/api/events?token=" + encodeURIComponent(token));
  sse.onopen = () => setConn(true, "на связи");
  sse.onerror = () => setConn(false, "нет связи");
  sse.onmessage = (ev) => {
    let msg = {};
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === "log") appendJournalLine(msg.line);
    if (msg.type === "chat_delta") streamChunk(msg.text);
    if (msg.type === "chat_done") finishStream(msg);
    if (msg.type === "notification") showNotification(msg);
    if (msg.type === "status") renderState(msg.status);
  };
}
function setConn(on, text) {
  for (const [dot, label] of [[$("conn-dot"), $("conn-text")], [$("m-conn-dot"), $("m-conn-text")]]) {
    dot.classList.toggle("on", on);
    label.textContent = text;
  }
}

/* ------------------------------------------------------- notifications */

function ensureNotifications() {
  if (!("Notification" in window)) { flashBanner("Уведомления не поддерживаются этим браузером"); return; }
  Notification.requestPermission().then((p) => {
    if (p === "granted") flashBanner("Уведомления включены");
    else flashBanner("Уведомления не разрешены в настройках браузера");
  });
}
$("btn-notify").addEventListener("click", ensureNotifications);

function showNotification(msg) {
  const text = (msg.kind === "reminder" ? "Напоминание: " : "") + (msg.text || "");
  flashBanner(text, 8000);
  petSay("point");
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification("Алонтито", { body: text, tag: "alontito-reminder" }); } catch (e) {}
  }
}

let bannerTimer = null;
function flashBanner(text, ms = 4000) {
  const el = $("banner");
  el.textContent = text;
  el.classList.remove("hidden");
  clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => el.classList.add("hidden"), ms);
}

/* --------------------------------------------------------- state render */

function renderState(state) {
  if (!state) return;
  const brain = (state.agent || {}).brain || {};
  const on = brain.enabled && brain.reachable;
  const sub = on
    ? "Отвечает ваш собственный мозг: " + esc(brain.model || "локальная модель") + "."
    : "Свой мозг ещё не подключён — Алонтито отвечает навыками и не выдумывает.";
  $("chat-sub").textContent = sub;
  const train = (state.agent || {}).training || {};
  $("train-meta").textContent = train.examples != null
    ? `примеров ${dash(train.examples)} · одобрено ${dash(train.approved)}`
    : "—";
  $("facts-meta").textContent = (state.agent || {}).facts != null
    ? `${dash(state.agent.facts)} факт(ов)`
    : "";
}

/* ---------------------------------------------------------------- chat */

let streaming = null;
let pendingQuestion = null;            // свой вопрос: его отрисует POST, не SSE

function chatBubble(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + (role === "user" ? "user" : "assistant");
  div.textContent = text;
  const empty = $("chat-empty");
  if (empty) empty.classList.add("hidden");
  $("chat-log").appendChild(div);
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
  return div;
}

function streamChunk(text) {
  if (!streaming) streaming = chatBubble("assistant", "");
  streaming.textContent += text;
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
}

function finishStream(msg) {
  if (streaming) { streaming.remove(); streaming = null; }
  if (!msg || !msg.question || !msg.answer) return;
  if (pendingQuestion !== null && msg.question === pendingQuestion) {
    return;                            // POST спрашивающего отрисует ответ
  }
  chatBubble("user", msg.question);
  chatBubble("assistant", msg.answer);
  petSay("nod");
}

async function loadChatHistory() {
  try {
    const data = await api("/api/chat/history");
    const log = $("chat-log");
    log.innerHTML = "";
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.id = "chat-empty";
    empty.textContent = "Напишите Алонтито. Он помнит факты, ставит напоминания и отвечает — не выдумывая.";
    log.appendChild(empty);
    for (const m of data.messages || []) chatBubble(m.role === "user" ? "user" : "assistant", m.text);
  } catch (e) { /* без истории просто начнём с чистого листа */ }
}

async function sendChat() {
  const input = $("chat-text");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  chatBubble("user", text);
  pendingQuestion = text;
  petSay("think");
  try {
    const reply = await api("/api/chat", { method: "POST", json: { message: text, device: "web" } });
    if (streaming) { streaming.remove(); streaming = null; }
    pendingQuestion = null;
    chatBubble("assistant", reply.answer);
    if (reply.source === "offline") flashBanner("Мозг не подключён — это честный ответ без модели");
    petSay("nod");
  } catch (e) {
    pendingQuestion = null;
    if (e.message !== "401") chatBubble("assistant", "Ошибка: " + e.message);
  }
}
$("chat-send").addEventListener("click", sendChat);
$("chat-text").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });
document.querySelectorAll(".chip").forEach((c) =>
  c.addEventListener("click", () => { $("chat-text").value = c.textContent.replace("…", ""); $("chat-text").focus(); }));

/* --------------------------------------------------------- attachments */

$("btn-attach").addEventListener("click", () => $("file-input").click());
$("file-input").addEventListener("change", async () => {
  for (const file of $("file-input").files) {
    const kind = /\.(png|jpe?g|webp|gif)$/i.test(file.name) ? "image"
      : /\.(wav|m4a|ogg|oga|webm|mp3)$/i.test(file.name) ? "voice" : "file";
    try {
      await api("/api/inbox?kind=" + kind + "&filename=" + encodeURIComponent(file.name) +
                "&note=" + encodeURIComponent("из чата"), { method: "POST", body: file });
      flashBanner("Файл во «Входящие»: " + file.name);
    } catch (e) { flashBanner("Не удалось отправить: " + e.message); }
  }
  $("file-input").value = "";
});

let recorder = null, recChunks = [];
$("btn-mic").addEventListener("click", async () => {
  if (recorder && recorder.state === "recording") {
    recorder.stop();
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
    recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    recChunks = [];
    recorder.ondataavailable = (e) => recChunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(recChunks, { type: mime || "audio/webm" });
      const ext = mime ? ".webm" : ".wav";
      $("btn-mic").classList.remove("recording");
      $("attach-note").textContent = "";
      try {
        await api("/api/inbox?kind=voice&filename=voice" + ext + "&note=голосовое из чата",
                  { method: "POST", body: blob });
        flashBanner("Голосовое во «Входящие» — расшифрую, когда установлен Vosk");
      } catch (e) { flashBanner("Не удалось записать: " + e.message); }
    };
    recorder.start();
    $("btn-mic").classList.add("recording");
    $("attach-note").textContent = "запись идёт — нажмите на микрофон ещё раз";
  } catch (e) {
    flashBanner("Микрофон недоступен: " + e.message);
  }
});

/* --------------------------------------------------------------- memory */

async function loadMemory() {
  try {
    const [facts, training] = await Promise.all([api("/api/facts"), api("/api/training")]);
    renderFacts(facts.facts || []);
    renderTraining(training.pending || [], training.stats || {});
  } catch (e) { /* 401 handled globally */ }
}
$("btn-memory-refresh").addEventListener("click", loadMemory);

function renderFacts(facts) {
  const ul = $("facts-list");
  ul.innerHTML = "";
  if (!facts.length) {
    ul.innerHTML = '<li class="empty">Фактов пока нет. Скажите Алонтито «запомни: …» или добавьте здесь.</li>';
    return;
  }
  for (const f of facts.slice().reverse()) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="fact-text">${esc(f.text)}</span>` +
      `<span class="pill">${esc(f.status)}</span>` +
      `<span class="fact-src">${esc(f.source)} · ${esc(f.date)}</span>`;
    ul.appendChild(li);
  }
}

$("fact-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("fact-text");
  const text = input.value.trim();
  if (!text) return;
  try {
    await api("/api/facts", { method: "POST", json: { text } });
    input.value = "";
    loadMemory();
  } catch (err) { flashBanner("Не удалось: " + err.message); }
});

function renderTraining(pending, stats) {
  const ul = $("training-list");
  ul.innerHTML = "";
  $("training-meta").textContent =
    `примеров ${dash(stats.examples)} · одобрено ${dash(stats.approved)}`;
  if (!pending.length) {
    ul.innerHTML = '<li class="empty">Кандидатов нет: они появляются после ответов мозга — ваш ✓ отправляет их в датасет.</li>';
    return;
  }
  for (const p of pending) {
    const li = document.createElement("li");
    li.className = "train-item";
    li.innerHTML =
      `<div class="train-q">${esc(p.prompt)}</div>` +
      `<div class="train-a">${esc(p.answer)}</div>` +
      `<div class="train-src">${esc(p.source)}</div>` +
      `<div class="train-actions">` +
      `<button class="btn" data-approve="${esc(p.fingerprint)}">✓ в датасет</button>` +
      `<button class="btn btn-quiet" data-dismiss="${esc(p.fingerprint)}">✗</button></div>`;
    ul.appendChild(li);
  }
  ul.querySelectorAll("[data-approve]").forEach((b) => b.addEventListener("click", async () => {
    await api("/api/training/approve", { method: "POST", json: { fingerprint: b.dataset.approve } });
    loadMemory();
  }));
  ul.querySelectorAll("[data-dismiss]").forEach((b) => b.addEventListener("click", async () => {
    await api("/api/training/dismiss", { method: "POST", json: { fingerprint: b.dataset.dismiss } });
    loadMemory();
  }));
}

$("btn-export").addEventListener("click", async () => {
  try {
    const r = await api("/api/training/export", { method: "POST" });
    flashBanner(`Выгружено в датасет: ${dash(r.exported)} · осталось ${dash(r.pending)}`);
  } catch (e) { flashBanner("Не удалось выгрузить: " + e.message); }
});

/* --------------------------------------------------------- automations */

async function loadAutomations() {
  try {
    const data = await api("/api/automations");
    const ul = $("auto-list");
    ul.innerHTML = "";
    const items = data.items || [];
    if (!items.length) {
      ul.innerHTML = '<li class="empty">Напоминаний нет. Пример: «напомни завтра в 9:00 позвонить».</li>';
      return;
    }
    for (const a of items) {
      const li = document.createElement("li");
      li.className = "row auto-item";
      li.innerHTML = `<div class="what"><strong>${esc(a.text)}</strong>` +
        `<span class="auto-when">${esc(a.when)}</span></div>` +
        `<div class="auto-actions">` +
        `<button class="btn btn-quiet" data-run="${esc(a.id)}">Сработать</button>` +
        `<button class="btn btn-quiet" data-del="${esc(a.id)}">Убрать</button></div>`;
      ul.appendChild(li);
    }
    ul.querySelectorAll("[data-run]").forEach((b) => b.addEventListener("click", async () => {
      await api("/api/automations/" + b.dataset.run + "/run", { method: "POST" });
      flashBanner("Уведомление отправлено на устройства");
    }));
    ul.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      await api("/api/automations/" + b.dataset.del, { method: "DELETE" });
      loadAutomations();
    }));
  } catch (e) { /* 401 handled globally */ }
}

$("auto-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("auto-text");
  const text = input.value.trim();
  if (!text) return;
  try {
    await api("/api/automations", { method: "POST", json: { text } });
    input.value = "";
    loadAutomations();
  } catch (err) { flashBanner(err.message); }
});

/* --------------------------------------------------------------- inbox */

function fileUrl(item) {
  return "/api/inbox/" + esc(item.id) + "/file?token=" +
    encodeURIComponent(localStorage.getItem("pult_token") || "");
}

async function loadInbox() {
  try {
    const data = await api("/api/inbox?limit=100");
    const ul = $("inbox-list");
    ul.innerHTML = "";
    const items = (data.items || []).slice().reverse();
    if (!items.length) {
      ul.innerHTML = '<li class="empty">Пусто. С телефона сюда можно прислать фото, голосовое или файл.</li>';
      return;
    }
    for (const it of items) {
      const li = document.createElement("li");
      li.className = "inbox-item";
      const kindIcon = { text: "заметка", voice: "голос", image: "фото", file: "файл" }[it.kind] || it.kind;
      const statePill = it.state === "new" ? '<span class="pill pill-new">новое</span>' : "";
      let body = "";
      if (it.kind === "text") body = `<div class="inbox-text">${esc(it.text)}</div>`;
      else if (it.kind === "image") body = `<img class="inbox-img" src="${fileUrl(it)}" alt="фото">`;
      else if (it.kind === "voice") body = `<audio controls preload="none" src="${fileUrl(it)}"></audio>`;
      else body = `<div class="inbox-text">файл: ${esc(it.filename)} (${Math.round((it.size || 0) / 1024)} КБ)</div>`;
      li.innerHTML =
        `<div class="inbox-head"><span class="pill">${kindIcon}</span>` +
        `<span class="fresh">${esc(fmtTime(it.ts))} · ${Math.round((it.size || 0) / 1024)} КБ</span>${statePill}</div>` +
        body +
        (it.transcript ? `<div class="inbox-transcript">«${esc(it.transcript)}»</div>` : "") +
        `<div class="inbox-actions">` +
        (it.kind === "voice" && !it.transcript ? `<button class="btn btn-quiet" data-trans="${esc(it.id)}">Расшифровать</button>` : "") +
        (it.state !== "processed" ? `<button class="btn btn-quiet" data-process="${esc(it.id)}">Передать агенту</button>` : "") +
        `<button class="btn btn-quiet" data-dismiss="${esc(it.id)}">Обработано</button></div>`;
      ul.appendChild(li);
    }
    ul.querySelectorAll("[data-trans]").forEach((b) => b.addEventListener("click", async () => {
      const r = await api("/api/inbox/" + b.dataset.trans + "/transcript", { method: "POST" });
      flashBanner(r.transcript ? "«" + r.transcript + "»" : (r.detail || "расшифровка недоступна"));
      loadInbox();
    }));
    ul.querySelectorAll("[data-process]").forEach((b) => b.addEventListener("click", async () => {
      const r = await api("/api/inbox/" + b.dataset.process + "/process", { method: "POST" });
      flashBanner(r.ok ? ("Агент: " + r.answer) : (r.detail || "не удалось обработать"));
      loadInbox();
    }));
    ul.querySelectorAll("[data-dismiss]").forEach((b) => b.addEventListener("click", async () => {
      await api("/api/inbox/" + b.dataset.dismiss + "/state", { method: "POST", json: { state: "processed" } });
      loadInbox();
    }));
  } catch (e) { /* 401 handled globally */ }
}
$("btn-inbox-refresh").addEventListener("click", loadInbox);

/* -------------------------------------------------------------- journal */

let journalLines = [];
function appendJournalLine(line) {
  journalLines.push(line);
  if (journalLines.length > 400) journalLines = journalLines.slice(-400);
  const term = $("term");
  if (term) {
    const div = document.createElement("div");
    div.className = "term-line";
    div.innerHTML = `<span class="term-note">${esc(line.slice(0, 19))}</span><span>${esc(line.slice(20))}</span>`;
    term.appendChild(div);
    while (term.childNodes.length > 400) term.removeChild(term.firstChild);
    term.scrollTop = term.scrollHeight;
  }
}
async function loadJournal() {
  try {
    const data = await api("/api/journal?n=200");
    const term = $("term");
    term.innerHTML = "";
    journalLines = [];
    for (const row of data.lines || []) appendJournalLine(row.line || row);
  } catch (e) { /* 401 handled globally */ }
}

/* ------------------------------------------------------------------ pet */

function petSend(msg) {
  const frame = $("pet-frame");
  if (frame && frame.contentWindow) {
    try { frame.contentWindow.postMessage({ pet: JSON.stringify(msg) }, "*"); } catch (e) {}
  }
}

function petSay(action) {
  petSend({ cmd: "play", clip: action });
}

// Интро шлём ПОСЛЕ «ready» от сцены — как на рабочем столе: команды до
// инициализации GLTF молча теряются.
function petIntro() {
  petSend({ cmd: "fps", value: 30 });
  petSend({ cmd: "play", clip: "idle", loop: true });
  petSend({ cmd: "face", deg: 0 });
  petSend({ cmd: "dissolve", dir: "in" });
}
window.addEventListener("message", (e) => {
  const d = e.data;
  if (!d || !d.pet) return;
  try {
    if (JSON.parse(d.pet).ev === "ready") setTimeout(petIntro, 150);
  } catch (err) {}
});
document.querySelectorAll("[data-pet-clip]").forEach((b) =>
  b.addEventListener("click", () => petSay(b.dataset.petClip)));

/* ------------------------------------------------------------- settings */

async function loadSettings() {
  await renderDevices();
  try {
    const state = await api("/api/state");
    const brain = (state.agent || {}).brain || {};
    $("brain-meta").textContent = brain.enabled
      ? (brain.reachable ? "подключён" : "недоступен")
      : "выключен";
    $("brain-status").textContent = brain.enabled
      ? (brain.reachable
          ? `свой мозг отвечает: ${brain.model}${brain.model_present ? "" : " (модель пока не найдена на сервере)"}`
          : `эндпоинт ${brain.endpoint} не отвечает${brain.detail ? ": " + brain.detail : ""}`)
      : "Свой мозг не подключён: config/alontito_brain.json → own.enabled. Пока он не готов, агент отвечает навыками и собирает данные для обучения.";
  } catch (e) { /* 401 handled globally */ }
}

async function renderDevices() {
  const ul = $("devices-list");
  ul.innerHTML = "";
  if (!isMaster) {
    ul.innerHTML = '<li class="empty">Управление устройствами — с ПК (мастер-токен).</li>';
    $("btn-pair-code").disabled = true;
    return;
  }
  $("btn-pair-code").disabled = false;
  try {
    const data = await api("/api/devices");
    const devices = data.devices || [];
    if (!devices.length) { ul.innerHTML = '<li class="empty">Пока только этот ПК.</li>'; return; }
    for (const d of devices) {
      const li = document.createElement("li");
      li.className = "row";
      li.innerHTML = `<div class="what"><strong>${esc(d.name)}</strong>` +
        `<span class="fresh">последний вход: ${dash(d.last_seen ? fmtTime(d.last_seen) : "—")}</span></div>` +
        `<div class="auto-actions"><button class="btn btn-quiet" data-revoke="${esc(d.id)}">${d.enabled ? "Отозвать" : "Вернуть"}</button></div>`;
      ul.appendChild(li);
    }
    ul.querySelectorAll("[data-revoke]").forEach((b) => b.addEventListener("click", async () => {
      const d = (await api("/api/devices")).devices.find((x) => x.id === b.dataset.revoke);
      await api("/api/devices/" + b.dataset.revoke + "/" + (d && d.enabled ? "revoke" : "enable"), { method: "POST" });
      renderDevices();
    }));
  } catch (e) {
    ul.innerHTML = '<li class="empty">Список недоступен: ' + esc(e.message) + "</li>";
  }
}

let pairTimer = null;
$("btn-pair-code").addEventListener("click", async () => {
  try {
    const r = await api("/api/pairing-code", { method: "POST" });
    $("pair-code").textContent = r.code;
    let left = r.expires_in;
    clearInterval(pairTimer);
    pairTimer = setInterval(() => {
      left -= 1;
      $("pair-timer").textContent = left > 0 ? `действует ${left} с` : "истёк";
      if (left <= 0) clearInterval(pairTimer);
    }, 1000);
  } catch (e) { flashBanner("Код может дать только ПК: " + e.message); }
});

/* ---------------------------------------------------------------- boot */

let booted = false;
async function boot() {
  if (booted) return;
  booted = true;
  try {
    const probe = await api("/api/state");
    renderState(probe);
    isMaster = true;                     // /api/devices solves it in settings
    connectSSE();
    loadChatHistory();
    openTab("chat");
  } catch (e) {
    if (e.message !== "401") flashBanner("Ошибка загрузки: " + e.message);
  }
}

(function init() {
  const urlToken = new URL(location.href).searchParams.get("token");
  const stored = localStorage.getItem("pult_token");
  if (urlToken) {
    tryToken(urlToken, localStorage.getItem("pult_device") || "").catch((e) => {
      localStorage.removeItem("pult_token");
      showLogin(e.message);
    });
  } else if (stored) {
    boot();
  } else {
    showLogin("");
  }
})();

/* ------------------------------------------------------------------ PWA */

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
