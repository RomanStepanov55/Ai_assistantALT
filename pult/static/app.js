/* Alontito Pult — frontend logic (vanilla JS, no build step) */
(function () {
  "use strict";

  // ---- token ------------------------------------------------------------
  var TOKEN = new URLSearchParams(location.search).get("token")
           || localStorage.getItem("pult_token") || "";
  function saveToken(t) { TOKEN = t; localStorage.setItem("pult_token", t); }
  function showLogin(msg) {
    document.getElementById("login").classList.remove("hidden");
    document.getElementById("login-error").textContent = msg || "";
  }
  if (!TOKEN) showLogin();
  document.getElementById("token-save").onclick = function () {
    var v = document.getElementById("token-input").value.trim();
    if (v) { saveToken(v); location.reload(); }
  };
  // токен из ссылки сохраняем и убираем из адресной строки: перезагрузка
  // страницы не выкидывает на экран входа, а ссылка с токеном не остаётся
  // в истории браузера
  if (TOKEN && new URLSearchParams(location.search).get("token")) {
    saveToken(TOKEN);
    try { history.replaceState(null, "", location.pathname); } catch (e) {}
  }

  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ "X-Pult-Token": TOKEN }, opts.headers || {});
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
      opts.headers["Content-Type"] = "application/json";
    }
    return fetch(path, opts).then(function (r) {
      if (r.status === 401) { showLogin("Неверный токен"); throw new Error("auth"); }
      return r.json();
    });
  }

  // ---- tabs ---------------------------------------------------------------
  document.querySelectorAll(".tab").forEach(function (b) {
    b.onclick = function () {
      document.querySelectorAll(".tab").forEach(function (x) { x.classList.remove("active"); });
      document.querySelectorAll(".page").forEach(function (x) { x.classList.remove("active"); });
      b.classList.add("active");
      document.getElementById("tab-" + b.dataset.tab).classList.add("active");
      if (b.dataset.tab === "memory") loadMemory();
      if (b.dataset.tab === "knowledge") loadKnowledge();
    };
  });

  // ---- status / dashboard ---------------------------------------------------
  var lastStatus = null;
  function renderStatus(s) {
    lastStatus = s;
    var dot = document.getElementById("conn-dot");
    var met = (s.last_health && s.last_health.metrics) || {};
    dot.classList.toggle("on", !!s.attached);
    document.getElementById("conn-text").textContent =
      s.attached ? "в игре · " + s.time : "нет окна · " + s.time;
    document.getElementById("c-strength").textContent =
      met.strength != null ? Number(met.strength).toLocaleString("ru-RU") : "—";
    document.getElementById("c-gems").textContent =
      met.gems != null ? Number(met.gems).toLocaleString("ru-RU") : "—";
    document.getElementById("c-attached").textContent = s.attached ? "да" : "нет";
    document.getElementById("c-sessions").textContent =
      (s.memory && s.memory.sessions) || 0;
    drawChart(s.telemetry || []);
  }

  function drawChart(points) {
    var cv = document.getElementById("chart");
    var dpr = window.devicePixelRatio || 1;
    var w = cv.clientWidth, h = 90;
    cv.width = w * dpr; cv.height = h * dpr;
    var ctx = cv.getContext("2d"); ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "#23234a"; ctx.beginPath();
    for (var y = 15; y < h; y += 25) { ctx.moveTo(0, y); ctx.lineTo(w, y); }
    ctx.stroke();
    var vals = points.map(function (p) { return p.strength; })
                     .filter(function (v) { return v != null; });
    if (vals.length < 2) return;
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    var span = (max - min) || 1;
    function X(i) { return 8 + i * (w - 16) / (points.length - 1); }
    function Y(v) { return h - 12 - (v - min) / span * (h - 28); }
    var grad = ctx.createLinearGradient(0, 0, w, 0);
    grad.addColorStop(0, "#4cf0ff"); grad.addColorStop(1, "#c060ff");
    ctx.strokeStyle = grad; ctx.lineWidth = 2; ctx.beginPath();
    points.forEach(function (p, i) {
      if (p.strength == null) return;
      var x = X(i), y = Y(p.strength);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = "#8a8fb8"; ctx.font = "10px monospace";
    ctx.fillText(max.toLocaleString("ru-RU"), 8, 12);
    ctx.fillText(min.toLocaleString("ru-RU"), 8, h - 2);
  }

  // frame: <img> не умеет заголовок X-Pult-Token, поэтому токен идёт в query
  // (в исходном обновлении кадр всегда получал 401 и не показывался).
  // Пока кадра нет, <img> скрыт: иначе браузер рисует значок битой картинки.
  function refreshFrame() {
    var img = document.getElementById("frame");
    var empty = document.getElementById("frame-empty");
    img.onload = function () {
      img.classList.remove("hidden");
      empty.style.display = "none";
    };
    img.onerror = function () {
      img.classList.add("hidden");
      empty.style.display = "flex";
    };
    img.src = "/api/frame?ts=" + Date.now() + "&token=" + encodeURIComponent(TOKEN);
  }
  setInterval(refreshFrame, 10000); refreshFrame();

  document.getElementById("btn-refresh").onclick = function () {
    api("/api/status").then(renderStatus).catch(function () {});
    refreshFrame();
  };

  // ---- actions (только чтение: HUD и наблюдение сцены) -----------------------
  function showResult(obj) {
    document.getElementById("action-result").textContent =
      typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
  }
  document.querySelectorAll(".act[data-action]").forEach(function (b) {
    b.onclick = function () {
      b.disabled = true;
      api("/api/action", { method: "POST", body: { name: b.dataset.action } })
        .then(showResult)
        .catch(function (e) { showResult("ошибка: " + e); })
        .finally(function () { b.disabled = false; });
    };
  });

  // ---- chat ------------------------------------------------------------------
  var chatLog = document.getElementById("chat-log");
  function addMsg(role, text) {
    var d = document.createElement("div");
    d.className = "msg " + role;
    d.textContent = text;
    chatLog.appendChild(d);
    d.scrollIntoView({ block: "nearest" });
  }
  function sendChat(text) {
    if (!text.trim()) return;
    addMsg("user", text);
    api("/api/chat", { method: "POST", body: { message: text } })
      .then(function (r) { addMsg("assistant", r.answer || r.error); })
      .catch(function (e) { addMsg("assistant", "ошибка: " + e); });
  }
  document.getElementById("chat-send").onclick = function () {
    var inp = document.getElementById("chat-text");
    sendChat(inp.value); inp.value = "";
  };
  document.getElementById("chat-text").addEventListener("keydown", function (e) {
    if (e.key === "Enter") document.getElementById("chat-send").click();
  });
  document.querySelectorAll(".chip").forEach(function (c) {
    c.onclick = function () { sendChat(c.textContent); };
  });
  api("/api/chat/history").then(function (r) {
    (r.messages || []).forEach(function (m) { addMsg(m.role, m.text); });
  }).catch(function () {});

  // ---- terminal (SSE) ----------------------------------------------------------
  var term = document.getElementById("term");
  function termLine(txt, cls) {
    var d = document.createElement("div");
    if (cls) d.style.color = cls;
    d.textContent = txt;
    term.appendChild(d);
    while (term.childNodes.length > 400) term.removeChild(term.firstChild);
    term.scrollTop = term.scrollHeight;
  }
  function connectSSE() {
    var es = new EventSource("/api/events?token=" + encodeURIComponent(TOKEN));
    es.onmessage = function (e) {
      try {
        var ev = JSON.parse(e.data);
        if (ev.type === "log") termLine("[" + ev.source + "] " + ev.line);
        if (ev.type === "status") renderStatus(ev.status);
      } catch (err) {}
    };
    es.onerror = function () {
      termLine("…переподключение…", "#8a8fb8");
      es.close(); setTimeout(connectSSE, 3000);
    };
  }
  connectSSE();

  // ---- memory -------------------------------------------------------------------
  function loadMemory() {
    api("/api/memory").then(function (m) {
      var tb = document.querySelector("#skills-table tbody");
      tb.innerHTML = "";
      // имена навыков приходят из памяти агента: только textContent, не innerHTML
      (m.skills || []).forEach(function (s) {
        var tr = document.createElement("tr");
        [String(s.name), String(s.attempts),
         s.success_rate == null ? "—" : Math.round(s.success_rate * 100) + "%",
         s.per_minute == null ? "—" : Math.round(s.per_minute).toLocaleString("ru-RU")
        ].forEach(function (text) {
          var td = document.createElement("td");
          td.textContent = text;
          tr.appendChild(td);
        });
        tb.appendChild(tr);
      });
      document.getElementById("facts-count").textContent =
        "всего: " + (m.facts || 0) + ", открыто вопросов: " + (m.questions_open || 0);
    }).catch(function () {});
  }
  document.getElementById("btn-chat-history").onclick = function () {
    api("/api/chat/history").then(function (r) {
      var ul = document.getElementById("chat-history-list");
      ul.innerHTML = "";
      (r.messages || []).slice(-30).forEach(function (m) {
        var li = document.createElement("li");
        li.textContent = (m.role === "user" ? "Вы: " : "Алонтито: ") + m.text;
        ul.appendChild(li);
      });
    });
  };

  // ---- knowledge -------------------------------------------------------------------
  function loadKnowledge() {
    api("/api/knowledge").then(function (k) {
      document.getElementById("kb-dir").textContent = k.dir + (k.ok ? "" : " — не найдена");
      document.getElementById("kb-lessons-n").textContent = k.lessons_total || 0;
      var ul = document.getElementById("kb-lessons");
      ul.innerHTML = "";
      (k.lessons || []).forEach(function (l) {
        var li = document.createElement("li");
        li.textContent = (l.ts || "") + " · " + (l.kind || "") + " · " + (l.text || JSON.stringify(l));
        ul.appendChild(li);
      });
      var uf = document.getElementById("kb-facts");
      uf.innerHTML = "";
      (k.facts || []).forEach(function (f) {
        var li = document.createElement("li");
        li.textContent = f.text || JSON.stringify(f);
        uf.appendChild(li);
      });
    }).catch(function () {});
  }

  // ---- boot -------------------------------------------------------------------
  api("/api/status").then(renderStatus).catch(function () {});
})();
