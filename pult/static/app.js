/* Alontito Pult — логика фронтенда (vanilla JS, без сборки и зависимостей).
   Пульт только читает: два действия («Проверка HUD», «Наблюдать сцену») —
   единственные, которые знает сервер, и других кнопок здесь нет. */
(function () {
  "use strict";

  var FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  var THEME_KEY = "pult_theme";
  var THEME_MODES = ["system", "light", "dark"];
  var THEME_LABEL = { system: "как в системе", light: "светлая", dark: "тёмная" };
  var THEME_ICON = {
    system: '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="3" width="11" height="8" rx="1"/><path d="M5.5 13.5h5M8 11v2.5"/></svg>',
    light: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1"/></svg>',
    dark: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z"/></svg>'
  };

  // ---- helpers ------------------------------------------------------------
  function $(id) { return document.getElementById(id); }
  function setText(id, text) { var el = $(id); if (el) el.textContent = text; }
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function fmtInt(v) { return Number(v).toLocaleString("ru-RU"); }
  function plural(n, one, few, many) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }
  function hhmm(t) {
    if (!t) return "";
    var d = new Date(t * 1000);
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }
  function fmtShort(v) {
    var a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + " млн";
    if (a >= 1e3) return (v / 1e3).toLocaleString("ru-RU", { maximumFractionDigits: 0 }) + " тыс";
    return String(Math.round(v));
  }

  // ---- token ------------------------------------------------------------
  var TOKEN = new URLSearchParams(location.search).get("token")
           || localStorage.getItem("pult_token") || "";
  function saveToken(t) { TOKEN = t; localStorage.setItem("pult_token", t); }
  function showLogin(msg) {
    $("login").classList.remove("hidden");
    $("login-error").textContent = msg || "";
  }
  if (!TOKEN) showLogin();
  $("token-save").onclick = function () {
    var v = $("token-input").value.trim();
    if (v) { saveToken(v); location.reload(); }
  };
  // токен из ссылки сохраняем и убираем из адресной строки: перезагрузка
  // не выкидывает на экран входа, а ссылка с токеном не остаётся в истории
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
      if (r.status === 401) {
        // «Неверный токен» только после реальной попытки: при первом входе
        // без токена экран входа молчит
        if (TOKEN) showLogin("Неверный токен");
        throw new Error("auth");
      }
      return r.json();
    });
  }

  // ---- theme ---------------------------------------------------------------
  var lastStatus = null;

  function themeMode() {
    try {
      var m = localStorage.getItem(THEME_KEY);
      return THEME_MODES.indexOf(m) >= 0 ? m : "system";
    } catch (e) {
      return "system";
    }
  }

  function applyTheme() {
    var mode = themeMode();
    var dark = mode === "dark" ||
      (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#161615" : "#f5f5f4");
    var icon = $("theme-icon");
    if (icon) icon.innerHTML = THEME_ICON[mode];
    setText("theme-label", "Тема: " + THEME_LABEL[mode]);
    var btn = $("btn-theme");
    if (btn) btn.setAttribute("aria-label", "Тема: " + THEME_LABEL[mode] + ". Сменить тему");
    if (lastStatus) drawChart(lastStatus.telemetry || []);   // цвета берутся из токенов
  }

  // ---- navigation ----------------------------------------------------------
  var sidebar = $("sidebar");

  function setMenu(open) {
    if (!sidebar) return;
    sidebar.classList.toggle("open", open);
    var b = $("btn-menu");
    if (b) b.setAttribute("aria-expanded", open ? "true" : "false");
  }

  document.querySelectorAll(".tab").forEach(function (b) {
    b.onclick = function () {
      document.querySelectorAll(".tab").forEach(function (x) {
        x.classList.remove("active");
        x.removeAttribute("aria-current");
      });
      document.querySelectorAll(".page").forEach(function (x) { x.classList.remove("active"); });
      b.classList.add("active");
      b.setAttribute("aria-current", "page");
      $("tab-" + b.dataset.tab).classList.add("active");
      setMenu(false);
      if (b.dataset.tab === "memory") { loadMemory(); loadChatHistory(); }
      if (b.dataset.tab === "knowledge") loadKnowledge();
      // у скрытой панели canvas без ширины: рисуем, когда она снова видна
      if (b.dataset.tab === "panel" && lastStatus) drawChart(lastStatus.telemetry || []);
    };
  });
  $("btn-menu").onclick = function () { setMenu(!sidebar.classList.contains("open")); };
  $("btn-menu-close").onclick = function () { setMenu(false); };
  $("canvas").addEventListener("click", function (e) {
    if (sidebar.classList.contains("open") && !sidebar.contains(e.target)) setMenu(false);
  });

  // ---- status --------------------------------------------------------------
  function setKpi(id, value) {
    var el = $(id);
    if (!el) return;
    if (value == null) {                    // числа нет — тихое тире
      el.textContent = "—";
      el.classList.add("missing");
    } else {
      el.textContent = fmtInt(value);
      el.classList.remove("missing");
    }
  }

  function setConn(on) {
    ["conn-dot", "m-conn-dot"].forEach(function (id) {
      var d = $(id);
      if (d) d.classList.toggle("on", on);
    });
    // время живёт в штампе страницы; здесь — только состояние глаза
    setText("conn-text", on ? "видит окно игры" : "окно игры не найдено");
    setText("m-conn-text", on ? "в игре" : "нет окна");
  }

  function renderStatus(s) {
    lastStatus = s;
    var met = (s.last_health && s.last_health.metrics) || {};
    var on = !!s.attached;
    setConn(on);
    var st = $("c-attached");
    if (st) {
      st.textContent = on ? "окно игры видно" : "окно игры не найдено";
      st.classList.toggle("on", on);
    }
    setText("panel-sub", on
      ? "Алонтито смотрит на игру и ничего не нажимает."
      : "Алонтито не играет за вас: откройте игру, и он будет только смотреть.");
    setText("stamp", s.time ? "обновлено в " + s.time : "ещё нет данных");
    setKpi("c-strength", met.strength);
    setKpi("c-gems", met.gems);
    setKpi("c-sessions", s.memory && s.memory.sessions != null ? s.memory.sessions : 0);
    var age = s.frame_age;
    if (age == null) setText("frame-meta", "нет кадра");
    else setText("frame-meta", "кадр " + Math.round(age) + " " +
      plural(Math.round(age), "секунду", "секунды", "секунд") + " назад");
    drawChart(s.telemetry || []);
  }

  // ---- chart ---------------------------------------------------------------
  // Одна линия чернилами, прямые сегменты, без заливки. 3–5 бледных горизонтальных
  // рисок с круглыми шагами, ноль подписей там, где читать нечего.
  function niceStep(raw) {
    var p = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var m = raw / p;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
  }

  function drawChart(points) {
    var cv = $("chart");
    var w = cv.clientWidth, h = cv.clientHeight || 200;
    if (!w) return;                         // панель сейчас скрыта
    var dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
    var ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.font = "11px " + FONT;
    var ink = cssVar("--text"), muted = cssVar("--muted");
    var grid = cssVar("--grid"), base = cssVar("--control");
    var padT = 12, padB = 24, padR = 12;

    function hline(x1, y, x2, color, width) {
      ctx.strokeStyle = color; ctx.lineWidth = width;
      ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke();
    }

    var vals = points.filter(function (p) { return p.strength != null; });
    setText("chart-meta", vals.length + " " + plural(vals.length, "замер", "замера", "замеров"));

    if (vals.length < 2) {                  // честная пустота: одна линия и подпись
      hline(0, h - padB + 0.5, w, grid, 1);
      ctx.fillStyle = muted; ctx.textAlign = "left"; ctx.textBaseline = "top";
      ctx.fillText(vals.length ? "Пока один замер: график появится со вторым."
                               : "Замеров силы пока нет.", 0, 8);
      return;
    }

    var lo = Infinity, hi = -Infinity;
    vals.forEach(function (p) {
      if (p.strength < lo) lo = p.strength;
      if (p.strength > hi) hi = p.strength;
    });
    if (lo === hi) { var spread = Math.max(Math.abs(lo) * 0.05, 1); lo -= spread; hi += spread; }

    var step = niceStep((hi - lo) / 4);
    var bottom = Math.floor(lo / step) * step;
    var top = Math.ceil(hi / step) * step;
    while (Math.round((top - bottom) / step) > 5) {   // не больше пяти рисок
      step *= 2;
      bottom = Math.floor(lo / step) * step;
      top = Math.ceil(hi / step) * step;
    }

    var plotH = h - padT - padB;
    var padL = Math.ceil(ctx.measureText(fmtShort(top)).width) + 16;
    var inner = w - padL - padR;
    function Y(v) { return padT + plotH - (v - bottom) / (top - bottom) * plotH; }

    // время идёт по временной шкале, если оно есть у точек
    var n = points.length;
    var t0 = points[0].t, t1 = points[n - 1].t;
    var byTime = t0 && t1 && t1 > t0;
    function X(i) {
      if (!byTime) return padL + inner * i / (n - 1);
      return padL + inner * (points[i].t - t0) / (t1 - t0);
    }

    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (var i = 0, ticks = Math.round((top - bottom) / step); i <= ticks; i++) {
      var t = bottom + i * step, y = Math.round(Y(t)) + 0.5;
      hline(padL, y, w - padR, i === 0 ? base : grid, 1);
      ctx.fillStyle = muted;
      ctx.fillText(fmtShort(t), padL - 8, y);
    }

    ctx.strokeStyle = ink; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.beginPath();
    var started = false, last = null;
    points.forEach(function (p, i) {
      if (p.strength == null) { started = false; return; }   // разрыв там, где замера не было
      var x = X(i), y = Y(p.strength);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      last = { x: x, y: y };
    });
    ctx.stroke();
    if (last) {                              // текущее значение — единственная точка
      ctx.fillStyle = ink;
      ctx.beginPath(); ctx.arc(last.x, last.y, 3, 0, Math.PI * 2); ctx.fill();
    }

    ctx.fillStyle = muted; ctx.textAlign = "center"; ctx.textBaseline = "top";
    var labels = Math.min(5, n);
    for (var k = 0; k < labels; k++) {
      var idx = labels === 1 ? 0 : Math.round(k * (n - 1) / (labels - 1));
      var x = Math.max(padL + 20, Math.min(w - padR - 20, X(idx)));
      ctx.fillText(hhmm(points[idx].t), x, h - padB + 8);
    }
  }

  var resizeTimer = 0;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (lastStatus) drawChart(lastStatus.telemetry || []);
    }, 120);
  });

  // ---- frame ---------------------------------------------------------------
  // <img> не умеет заголовок X-Pult-Token, поэтому токен идёт в query.
  // Пока кадра нет, <img> скрыт: иначе браузер рисует значок битой картинки.
  function refreshFrame() {
    var img = $("frame");
    var empty = $("frame-empty");
    img.onload = function () {
      img.classList.remove("hidden");
      empty.classList.add("hidden");
    };
    img.onerror = function () {
      img.classList.add("hidden");
      empty.classList.remove("hidden");
    };
    img.src = "/api/frame?ts=" + Date.now() + "&token=" + encodeURIComponent(TOKEN);
  }
  setInterval(refreshFrame, 10000); refreshFrame();

  function refreshAll() {
    api("/api/status").then(renderStatus).catch(function () {});
    refreshFrame();
    journalSoon();
  }
  $("btn-refresh").onclick = refreshAll;

  // ---- actions (только чтение: HUD и наблюдение сцены) -----------------------
  function showResult(obj) {
    var head = $("action-headline");
    var box = $("action-result");
    var isResult = obj && typeof obj === "object" && ("ok" in obj);
    var failed = isResult && !obj.ok;
    var error = isResult && !!obj.error;
    head.textContent = "";
    if (failed) {
      var small = document.createElement("small");
      small.textContent = error ? "Ошибка" : "Не получилось";
      head.appendChild(small);
    }
    head.appendChild(document.createTextNode(isResult ? (obj.detail || obj.error || "") : String(obj)));
    // красным — только настоящая ошибка; «окна нет» — не авария
    head.className = "headline" + (error ? " error" : "");
    box.textContent = typeof obj === "string" ? obj : JSON.stringify(obj, null, 2);
    box.classList.remove("hidden");
  }
  document.querySelectorAll("[data-action]").forEach(function (b) {
    b.onclick = function () {
      b.disabled = true;
      api("/api/action", { method: "POST", body: { name: b.dataset.action } })
        .then(showResult)
        .catch(function (e) { showResult("Не получилось: пульт не отвечает. " + e); })
        .finally(function () { b.disabled = false; refreshAll(); });
    };
  });

  // ---- chat ------------------------------------------------------------------
  var chatLog = $("chat-log");
  function addMsg(role, text) {
    var e = $("chat-empty");
    if (e) e.classList.add("hidden");
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
      .catch(function (e) { addMsg("assistant", "Пульт не отвечает: " + e); });
  }
  $("chat-send").onclick = function () {
    var inp = $("chat-text");
    sendChat(inp.value); inp.value = "";
  };
  $("chat-text").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chat-send").click(); }
  });
  document.querySelectorAll(".chip").forEach(function (c) {
    c.onclick = function () { sendChat(c.textContent); };
  });

  // ---- terminal (SSE) ----------------------------------------------------------
  var term = $("term");
  var termAtBottom = true;
  term.addEventListener("scroll", function () {
    termAtBottom = term.scrollHeight - term.scrollTop - term.clientHeight < 24;
  });
  function termAppend(node) {
    term.appendChild(node);
    while (term.childNodes.length > 400) term.removeChild(term.firstChild);
    if (termAtBottom) term.scrollTop = term.scrollHeight;   // не дёргаем листание
  }
  function termLine(source, line) {
    var d = document.createElement("div");
    d.className = "term-line";
    var s = document.createElement("span");
    s.className = "term-src";
    s.textContent = source;
    var l = document.createElement("span");
    l.className = "term-ln";
    l.textContent = line;
    d.appendChild(s); d.appendChild(l);
    termAppend(d);
  }
  function termNote(text) {
    var d = document.createElement("div");
    d.className = "term-note";
    d.textContent = text;
    termAppend(d);
  }
  function connectSSE() {
    var es = new EventSource("/api/events?token=" + encodeURIComponent(TOKEN));
    es.onmessage = function (e) {
      try {
        var ev = JSON.parse(e.data);
        if (ev.type === "log") termLine(ev.source, ev.line);
        if (ev.type === "status") renderStatus(ev.status);
      } catch (err) {}
    };
    es.onerror = function () {
      termNote("Переподключение к журналу…");
      es.close(); setTimeout(connectSSE, 3000);
    };
  }
  connectSSE();

  // ---- memory -------------------------------------------------------------------
  function cell(tr, text, cls) {
    var td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text;
    tr.appendChild(td);
  }
  function emptyItem(text) {
    var li = document.createElement("li");
    li.className = "empty";
    li.textContent = text;
    return li;
  }

  function loadMemory() {
    api("/api/memory").then(function (m) {
      var tb = document.querySelector("#skills-table tbody");
      tb.innerHTML = "";
      var skills = m.skills || [];
      if (!skills.length) {
        var tr = document.createElement("tr"), td = document.createElement("td");
        td.colSpan = 4;
        td.className = "empty-cell";
        td.textContent = "Навыков с замерами пока нет.";
        tr.appendChild(td); tb.appendChild(tr);
      }
      skills.forEach(function (s) {
        var tr = document.createElement("tr");
        cell(tr, String(s.name));
        cell(tr, s.attempts == null ? "—" : fmtInt(s.attempts), "num");
        cell(tr, s.success_rate == null ? "—" : Math.round(s.success_rate * 100) + "%", "num");
        cell(tr, s.per_minute == null ? "—" : fmtInt(Math.round(s.per_minute)), "num");
        tb.appendChild(tr);
      });
      setText("facts-count", "Фактов в памяти: " + fmtInt(m.facts || 0) +
        ", открытых вопросов: " + fmtInt(m.questions_open || 0) + ".");
    }).catch(function () {});
  }

  var HISTORY_ROWS = 8;                      // столько же строк, сколько влезает рядом
  function loadChatHistory() {
    api("/api/chat/history").then(function (r) {
      var all = r.messages || [];
      var shown = all.slice(-HISTORY_ROWS);
      var ul = $("chat-history-list");
      ul.innerHTML = "";
      if (!shown.length) {
        ul.appendChild(emptyItem("Переписки в пульте пока нет."));
      } else {
        shown.forEach(function (m) {
          var li = document.createElement("li");
          li.className = "row";
          var who = document.createElement("span");
          who.className = "who";
          who.textContent = m.role === "user" ? "Вы" : "Алонтито";
          var what = document.createElement("span");
          what.className = "what";
          what.textContent = m.text;
          li.appendChild(who); li.appendChild(what);
          ul.appendChild(li);
        });
      }
      setText("chat-history-meta", all.length > HISTORY_ROWS
        ? "показаны последние " + HISTORY_ROWS + " из " + all.length : "");
    }).catch(function () {});
  }
  $("btn-chat-history").onclick = loadChatHistory;

  // ---- knowledge -------------------------------------------------------------------
  function loadKnowledge() {
    api("/api/knowledge").then(function (k) {
      setText("kb-dir", (k.ok ? "База знаний: " : "База знаний не найдена: ") + k.dir);
      setText("kb-lessons-n", fmtInt(k.lessons_total || 0));
      var lessons = $("kb-lessons");
      lessons.innerHTML = "";
      if (!(k.lessons || []).length) {
        lessons.appendChild(emptyItem("Уроков в базе пока нет."));
      } else {
        (k.lessons || []).forEach(function (l) {
          var li = document.createElement("li");
          li.className = "row";
          var who = document.createElement("span");
          who.className = "who";
          who.textContent = l.kind || "";
          var what = document.createElement("span");
          what.className = "what";
          what.textContent = l.text || JSON.stringify(l);
          li.appendChild(who); li.appendChild(what);
          lessons.appendChild(li);
        });
      }
      var facts = $("kb-facts");
      facts.innerHTML = "";
      if (!(k.facts || []).length) {
        facts.appendChild(emptyItem("Фактов в базе пока нет."));
      } else {
        (k.facts || []).forEach(function (f) {
          var li = document.createElement("li");
          li.textContent = f.text || JSON.stringify(f);
          facts.appendChild(li);
        });
      }
    }).catch(function () {});
  }

  // ---- journal (последние записи глаза на панели) -----------------------------
  // Строка журнала:
  // [2026-11-22 19:42:33] Str: 3.11M | Gems: 16.1M | Gift: READY | Modal: none | Machine: - | Events: none
  var JOURNAL_RE = /^\[([^\]]+)\]\s*Str:\s*(.*?)\s*\|\s*Gems:\s*(.*?)\s*\|\s*Gift:\s*(.*?)\s*\|\s*Modal:\s*(.*?)\s*\|\s*Machine:\s*(.*?)\s*\|\s*Events:\s*(.*)$/i;
  var JOURNAL_ROWS = 5;

  // «-», «?» и пустота в журнале — это «числа нет», а не ноль
  function norm(v) {
    var s = (v || "").trim();
    return s === "" || s === "-" || s === "?" ? "—" : s;
  }

  function shortTime(stamp) {
    // "2026-11-22 19:42:33" -> "22.11 19:42"
    var m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2})/.exec(stamp || "");
    return m ? m[3] + "." + m[2] + " " + m[4] : (stamp || "—");
  }

  function jcell(tr, text, cls) {
    var td = document.createElement("td");
    td.className = cls;
    td.textContent = text;
    tr.appendChild(td);
  }
  function jspan(tr, text) {
    var td = document.createElement("td");
    td.colSpan = 6;
    td.className = "span";
    td.textContent = text;
    tr.appendChild(td);
  }

  function loadJournal() {
    api("/api/journal?n=" + (JOURNAL_ROWS + 11)).then(function (r) {
      var rows = (r.lines || [])
        .filter(function (l) { return l.source === "eye_journal.log"; })
        .slice(-JOURNAL_ROWS);
      var tb = $("journal-body");
      tb.innerHTML = "";
      if (!rows.length) {
        var emptyRow = document.createElement("tr");
        jspan(emptyRow, "Записей нет: Алонтито ещё ни разу не смотрел на игру.");
        tb.appendChild(emptyRow);
        return;
      }
      rows.forEach(function (l) {
        var m = JOURNAL_RE.exec(l.line);
        var tr = document.createElement("tr");
        if (!m) {
          jspan(tr, l.line);                       // «Roblox window NOT found» и т. п.
        } else {
          var strength = norm(m[2]), gems = norm(m[3]);
          var gift = norm(m[4]);
          if (gift === "READY") gift = "готов";
          var modal = norm(m[5]);
          if (modal === "none") modal = "нет";
          else if (/^open:/i.test(modal)) modal = "открыт";
          var events = norm(m[6]);
          if (events.toLowerCase() === "none") events = "—";
          jcell(tr, shortTime(m[1]), "time");
          jcell(tr, strength, "num" + (strength === "—" ? " quiet" : ""));
          jcell(tr, gems, "num" + (gems === "—" ? " quiet" : ""));
          jcell(tr, gift, gift === "—" ? "quiet" : "");
          jcell(tr, modal, modal === "нет" ? "quiet" : "");
          jcell(tr, events, events === "—" ? "quiet" : "");
        }
        tb.appendChild(tr);
      });
    }).catch(function () {});
  }

  var journalTimer = 0;
  function journalSoon() {
    if (journalTimer) return;
    journalTimer = setTimeout(function () { journalTimer = 0; loadJournal(); }, 5000);
  }

  // ---- boot -------------------------------------------------------------------
  $("btn-theme").onclick = function () {
    var next = THEME_MODES[(THEME_MODES.indexOf(themeMode()) + 1) % THEME_MODES.length];
    try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
    applyTheme();
  };
  var mq = window.matchMedia("(prefers-color-scheme: dark)");
  if (mq.addEventListener) mq.addEventListener("change", applyTheme);

  applyTheme();
  api("/api/status").then(renderStatus).catch(function () {});
  loadJournal();
})();
