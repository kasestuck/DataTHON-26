/* app.js — весь JavaScript сайта в одном файле. Разделы:
   1) ШАПКА И ФОН (капли, меню профиля, заставка) — на всех страницах
   2) РЕЗУЛЬТАТ: renderResult(data, root) — вердикт, карточки, кадры, окно правила
   3) ПЛЕЕР: initPlayer(file, root, violations), seekTo(sec)
   4) ЗАГРУЗКА И API: выбор файла, запросы, опрос, таймер, ошибки (только на index.html)
   Настройки и подключение к бэку — в config.js. */

/* ================= 1. ШАПКА И ФОН ================= */
/* ui.js — Front 2. Общее для всех страниц: капли на фоне, шапка с профилем, заставка. */
(function () {
  // --- капли
  const wrap = document.createElement("div");
  wrap.className = "blobs";
  const specs = [[170,"4%","-3%"],[120,"70%","58%"],[90,"82%","8%"],[70,"6%","62%"],[55,"38%","88%"],
    [130,"28%","10%"],[44,"60%","30%"],[100,"-4%","40%"],[150,"55%","74%"],[36,"48%","50%"]];
  const rot = [-18, 12, 25, -30, 8, -12, 20, -8, 15, -22];
  specs.forEach(([s, top, left], i) => {
    const b = document.createElement("i");
    b.className = "blob " + (i % 2 ? "blob--b" : "blob--a");
    b.style.cssText = `width:${s}px;top:${top};left:${left};animation-delay:${-i * 3.1}s;--r:${rot[i]}deg;` +
      `--dx:${(i % 2 ? -1 : 1) * (18 + i * 4)}px;--dy:${-20 - i * 5}px`;
    wrap.append(b);
  });
  document.body.prepend(wrap);

  // --- шапка
  const host = document.getElementById("site-header");
  if (host) {
    const page = location.pathname.split("/").pop() || "index.html";
    const name = ((function () { try { return localStorage.getItem("fv_name"); } catch (e) { return null; } })() || "Проверяющий").trim() || "Проверяющий";
    const link = (href, t) => `<a href="${href}" class="${page === href ? "is-active" : ""}">${t}</a>`;
    host.className = "glass header";
    host.innerHTML = `
      <a class="logo" href="index.html"><span class="logo-drop"></span>
        <span><b>Forte Vision</b> <small>ИИ-помощник проверки кассовой дисциплины</small></span></a>
      <nav>${link("index.html", "Главная")}${link("analyze.html", "ИИ-анализ")}${link("about.html", "О решении")}</nav>
      <div class="profile">
        <button class="avatar" type="button" aria-haspopup="true" aria-expanded="false" aria-label="Профиль"></button>
        <div class="glass menu is-hidden"><div class="who"></div>
          <a href="profile.html">Мой профиль</a></div>
      </div>`;
    const btn = host.querySelector(".avatar"), menu = host.querySelector(".menu");
    btn.textContent = name[0].toUpperCase();
    menu.querySelector(".who").textContent = name;
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = menu.classList.toggle("is-hidden") === false;
      btn.setAttribute("aria-expanded", open);
    });
    document.addEventListener("click", () => menu.classList.add("is-hidden"));
  }

  // --- заставка
  const splash = document.getElementById("splash");
  if (splash) {
    const t0 = Date.now();
    window.addEventListener("load", () =>
      setTimeout(() => { splash.classList.add("is-gone"); setTimeout(() => splash.remove(), 700); },
        Math.max(0, 1100 - (Date.now() - t0))));
  }
})();

/* ================= 2. РЕЗУЛЬТАТ ================= */
/* result.js — Front 2. Контракт: renderResult(data, rootElement)
   data — JSON из ТЗ; необязательное поле violation.rule = {document, point} или строка.
   Кнопка перемотки вызывает window.seekTo(sec) из player.js. */
(function () {
  const TEXT = {
    no_violation: "Нарушение не обнаружено",
    violation: "Обнаружены признаки возможных нарушений",
  };
  const NOTICE = "Результат является подсказкой для проверки человеком, а не юридическим или дисциплинарным заключением.";

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text; // textContent: защита от HTML-вставки
    return n;
  }
  function fmt(sec) {
    const s = Math.max(0, Number(sec) || 0), m = Math.floor(s / 60);
    return String(m).padStart(2, "0") + ":" + (s - m * 60).toFixed(1).padStart(4, "0");
  }
  function ruleText(r) {
    if (!r) return null;
    if (typeof r === "string") return r;
    return [r.document, r.point].filter(Boolean).join(", ") || null;
  }
  function openLightbox(src) {
    const box = el("div", "lightbox");
    const img = el("img"); img.src = src; img.alt = "Кадр-доказательство";
    box.append(img);
    const close = () => { box.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => e.key === "Escape" && close();
    box.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    document.body.append(box);
  }
  function openModal(v) {
    const rule = typeof v.rule === "object" && v.rule ? v.rule : { document: ruleText(v.rule) };
    const m = el("div", "modal");
    const box = el("div", "glass modal-box");
    const x = el("button", "modal-x", "×"); x.type = "button"; x.setAttribute("aria-label", "Закрыть");
    box.append(x, el("h3", null, v.type || "Нарушение"), el("p", "muted", "Правило и нормативный источник"));
    box.append(el("p", null, [rule.document, rule.point].filter(Boolean).join(" — ") || "Источник не указан"));
    if (rule.text) box.append(el("blockquote", null, rule.text));
    if (rule.url && /^https:\/\//.test(rule.url)) {
      const a = el("a", null, "Открыть документ"); a.href = rule.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      box.append(a);
    }
    box.append(el("p", "src", "Подсказка для проверки человеком, а не юридическое заключение."));
    m.append(box);
    const close = () => { m.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => e.key === "Escape" && close();
    x.addEventListener("click", close);
    m.addEventListener("click", (e) => e.target === m && close());
    document.addEventListener("keydown", onKey);
    document.body.append(m);
    x.focus();
  }
  function card(v, i, total) {
    const c = el("article", "glass card");
    const head = el("div", "card-head");
    const title = el("h3", null, (total > 1 ? (i + 1) + ". " : "") + (v.type || "Нарушение"));
    const seek = el("button", "btn btn--ghost btn--small", "Перейти к " + fmt(v.timecode_sec));
    seek.type = "button";
    seek.title = "Перемотать видео на этот момент";
    seek.addEventListener("click", () => {
      if (window.seekTo) window.seekTo(v.timecode_sec);
      const p = document.querySelector(".player");
      if (p) p.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    head.append(title, seek);
    c.append(head, el("p", null, v.description || ""));

    const frames = el("div", "frames");
    (v.evidence_frames || []).slice(0, 3).forEach((f) => {
      const b64 = String(f.image_base64 || "");
      const src = b64.startsWith("data:") ? b64 : "data:image/jpeg;base64," + b64;
      const b = el("button", "frame"); b.type = "button";
      b.setAttribute("aria-label", "Увеличить кадр " + fmt(f.timestamp_sec));
      const img = el("img"); img.src = src; img.alt = "Кадр на " + fmt(f.timestamp_sec);
      b.append(img, el("span", null, fmt(f.timestamp_sec)));
      b.addEventListener("click", () => openLightbox(src));
      frames.append(b);
    });
    if (frames.children.length) c.append(frames);

    const rule = el("div", "rule");
    rule.append(el("b", null, "Правило и источник"),
      el("span", null, ruleText(v.rule) || "Источник указан в описании"));
    if (ruleText(v.rule)) {
      const more = el("button", "btn btn--ghost btn--small", "Подробнее о правиле");
      more.type = "button"; more.addEventListener("click", () => openModal(v));
      rule.append(more);
    }
    c.append(rule);
    return c;
  }

  window.renderResult = function (data, root) {
    root.replaceChildren();
    const list = data.violations || [];
    const bad = data.verdict === "violation";
    const head = el("section", "glass verdict " + (bad ? "verdict--bad" : "verdict--ok"));
    head.setAttribute("role", "status");
    const left = el("div");
    left.append(el("div", "eyebrow", "Результат проверки"), el("h2", null, TEXT[bad ? "violation" : "no_violation"]));
    head.append(left);
    if (list.length > 1) { // счётчик нужен только если нарушений несколько
      const cnt = el("div", "counter", String(list.length)); cnt.title = "Найдено нарушений"; head.append(cnt);
    }
    root.append(head);

    const wrap = el("div", "results");
    list.forEach((v, i) => wrap.append(card(v, i, list.length)));
    root.append(wrap, el("p", "notice", NOTICE));
  };
})();

/* ================= 3. ПЛЕЕР ================= */
/* player.js — Front 2. Контракт: initPlayer(file, rootElement, violations); seekTo(sec)
   Видео играет из локального файла (URL.createObjectURL), с сервера не качается. */
(function () {
  let video = null, url = null;

  window.seekTo = function (sec) {
    if (!video) return;
    video.currentTime = Math.max(0, Number(sec) || 0);
    video.play().catch(() => {});
  };

  window.initPlayer = function (file, root, violations) {
    if (url) URL.revokeObjectURL(url);
    root.replaceChildren();
    root.classList.add("glass", "player");
    video = null;

    const stub = (msg) => {
      const d = document.createElement("div");
      d.className = "stub";
      d.textContent = msg;
      root.replaceChildren(d);
      video = null;
    };
    const probe = document.createElement("video");
    // .mov (QuickTime): не все браузеры его проигрывают — тогда заглушка, результат всё равно виден
    if (!file || !(probe.canPlayType("video/quicktime") || probe.canPlayType("video/mp4"))) {
      return stub("Ваш браузер не воспроизводит .mov. Результаты анализа ниже — таймкоды и кадры — доступны как обычно.");
    }
    url = URL.createObjectURL(file);
    video = probe;
    video.src = url;
    video.controls = true;
    video.preload = "metadata";
    video.setAttribute("aria-label", "Загруженное видео");
    video.addEventListener("error", () =>
      stub("Не удалось воспроизвести видео в браузере. Результаты анализа ниже доступны."));

    const bar = document.createElement("div");
    bar.className = "timeline";
    bar.setAttribute("aria-label", "Шкала времени с метками нарушений");
    const labels = document.createElement("div");
    labels.className = "timeline-labels";
    const playhead = document.createElement("div");
    playhead.className = "playhead";
    bar.append(playhead);
    root.append(video, bar, labels);
    const mmss = (x) => String(Math.floor(x / 60)).padStart(2, "0") + ":" + String(Math.floor(x % 60)).padStart(2, "0");

    video.addEventListener("loadedmetadata", () => {
      const dur = video.duration;
      if (!isFinite(dur) || dur <= 0) return;
      // мелкие палочки шкалы времени
      const step = [1, 2, 5, 10, 15, 30, 60, 120, 300].find((s) => dur / s <= 14) || 300;
      for (let t = step; t < dur; t += step) {
        const k = document.createElement("i");
        k.className = "tick";
        k.style.left = (t / dur) * 100 + "%";
        bar.append(k);
      }
      const l1 = document.createElement("span"), l2 = document.createElement("span");
      l1.textContent = "00:00"; l2.textContent = mmss(dur);
      labels.append(l1, l2);
      // крупные палочки — моменты нарушений
      (violations || []).forEach((v, i) => {
        const m = document.createElement("button");
        m.type = "button";
        m.className = "marker";
        m.style.left = Math.min(100, (v.timecode_sec / dur) * 100) + "%";
        m.title = (i + 1) + ". " + (v.type || "Нарушение") + " — " + v.timecode_sec + " с";
        m.setAttribute("aria-label", m.title);
        m.addEventListener("click", () => window.seekTo(v.timecode_sec));
        bar.append(m);
      });
      video.addEventListener("timeupdate", () => { playhead.style.left = (video.currentTime / dur) * 100 + "%"; });
      bar.addEventListener("click", (ev) => { // клик по шкале — перемотка
        if (ev.target.closest(".marker")) return;
        const r = bar.getBoundingClientRect();
        video.currentTime = Math.max(0, Math.min(dur, ((ev.clientX - r.left) / r.width) * dur));
      });
    });
  };

  /* Кадры-доказательства из загруженного видео: если бэк не прислал кадры (или force=true, как в демо),
     делаем скриншоты в момент нарушения: за 1 с до, в сам момент и через 1 с. Работает, если браузер умеет играть файл. */
  window.captureFrames = function (file, violations, force) {
    const jobs = [];
    (violations || []).forEach((v) => {
      if (force || !(v.evidence_frames && v.evidence_frames.length)) { v.evidence_frames = []; jobs.push(v); }
    });
    if (!jobs.length || !file) return Promise.resolve();
    return new Promise((resolve) => {
      const vid = document.createElement("video");
      const src = URL.createObjectURL(file);
      let done = false, queue = null, qi = 0, dur = 0;
      const finish = () => {
        if (done) return; done = true; clearTimeout(timer);
        vid.removeAttribute("src"); URL.revokeObjectURL(src); resolve();
      };
      const timer = setTimeout(finish, 5000); // не ждём дольше 5 секунд
      vid.muted = true; vid.preload = "auto"; vid.playsInline = true;
      vid.addEventListener("error", finish);
      function plan() {
        queue = [];
        jobs.forEach((v) => {
          const tc = Math.max(0, Number(v.timecode_sec) || 0);
          let ts = [tc - 1, tc, tc + 1].filter((t) => t >= 0.05 && (!dur || t <= dur - 0.1));
          if (!ts.length) ts = [Math.max(0.05, Math.min(tc, (dur || tc) - 0.1))];
          ts.forEach((t) => queue.push({ v, t: Math.round(t * 10) / 10 }));
        });
      }
      function next() {
        if (qi >= queue.length) return finish();
        vid.currentTime = queue[qi].t;
      }
      vid.addEventListener("loadedmetadata", () => { dur = isFinite(vid.duration) ? vid.duration : 0; plan(); next(); });
      vid.addEventListener("seeked", () => {
        const q = queue && queue[qi];
        if (!q) return finish();
        try {
          const w = vid.videoWidth, h = vid.videoHeight;
          if (w && h) {
            const k = Math.min(1, 640 / w), c = document.createElement("canvas");
            c.width = Math.round(w * k); c.height = Math.round(h * k);
            c.getContext("2d").drawImage(vid, 0, 0, c.width, c.height);
            q.v.evidence_frames.push({ timestamp_sec: q.t, image_base64: c.toDataURL("image/jpeg", 0.8).split(",")[1] });
          }
        } catch (err) { /* кадр не получился — пропускаем */ }
        qi++; next();
      });
      vid.src = src;
    });
  };
})();

/* ================= 4. ЗАГРУЗКА И API ================= */
/* app.js — выбор файла, проверка, запросы к API, опрос статуса, таймер, отмена, ошибки.
   Вызывает: initPlayer(file, root, violations) из player.js и renderResult(data, root) из result.js.
   Конфиг — config.js. В DEMO-режиме работает без бэка на mock.json. */
(function () {
  "use strict";
  if (!document.getElementById("screen-upload")) return; // about.html и profile.html: только шапка и фон
  const C = Object.assign(
    { DEMO: false, API_URL: "", API_KEY: "", POLL_MS: 2500, MAX_MB: 100, TIMEOUT_MS: 300000 },
    window.CONFIG || {}
  );
  const $ = (id) => document.getElementById(id);
  const screens = { upload: $("screen-upload"), processing: $("screen-processing"), result: $("screen-result") };
  const dz = $("dropzone"), input = $("file"), info = $("fileinfo"), fname = $("filename"),
        err = $("error"), check = $("check"), timerEl = $("timer"), statusEl = $("status");
  let file = null, run = null;

  /* ---------- интерфейс ---------- */
  function show(name) {
    Object.keys(screens).forEach((k) => screens[k].classList.toggle("is-hidden", k !== name));
  }
  function showError(msg) {
    err.textContent = msg || "";
    err.classList.toggle("is-hidden", !msg);
  }
  function setStatus(t) { statusEl.textContent = t; }
  function setFile(f) {
    file = null; showError("");
    if (f) {
      if (!/\.mov$/i.test(f.name)) showError("Нужен видеофайл в формате .mov");
      else if (f.size > C.MAX_MB * 1024 * 1024) showError("Файл слишком большой: максимум " + C.MAX_MB + " МБ");
      else if (f.size === 0) showError("Файл пустой");
      else file = f;
    }
    fname.textContent = file ? file.name + " · " + (file.size / 1048576).toFixed(1) + " МБ" : "";
    info.classList.toggle("is-hidden", !file);
    check.disabled = !file;
  }

  input.addEventListener("change", () => setFile(input.files[0]));
  ["dragenter", "dragover"].forEach((e) => dz.addEventListener(e, (ev) => { ev.preventDefault(); dz.classList.add("is-over"); }));
  ["dragleave", "drop"].forEach((e) => dz.addEventListener(e, (ev) => { ev.preventDefault(); dz.classList.remove("is-over"); }));
  dz.addEventListener("drop", (ev) => { if (ev.dataTransfer && ev.dataTransfer.files[0]) setFile(ev.dataTransfer.files[0]); });
  // чтобы браузер не открывал видео, если его уронили мимо зоны
  ["dragover", "drop"].forEach((e) => window.addEventListener(e, (ev) => ev.preventDefault()));
  $("clear").addEventListener("click", () => { input.value = ""; setFile(null); });

  /* ---------- вспомогательное ---------- */
  function abortError() { const e = new Error("aborted"); e.name = "AbortError"; return e; }
  function sleep(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal.aborted) return reject(abortError());
      const t = setTimeout(resolve, ms);
      signal.addEventListener("abort", () => { clearTimeout(t); reject(abortError()); }, { once: true });
    });
  }
  function apiUrl(path) { return String(C.API_URL || "").replace(/\/+$/, "") + path; }
  function authHeaders() { const h = {}; if (C.API_KEY) h["X-API-Key"] = C.API_KEY; return h; }

  function statusMessage(code) {
    if (code === 401 || code === 403) return "Неверный или отсутствующий ключ доступа (API-ключ).";
    if (code === 413) return "Файл слишком большой для сервиса.";
    if (code === 400 || code === 415 || code === 422) return "Сервис не принял файл. Убедитесь, что это видео в формате .mov.";
    if (code === 404) return "Задача не найдена на сервере. Попробуйте загрузить видео ещё раз.";
    if (code === 429) return "Сервис перегружен. Повторите попытку чуть позже.";
    if (code >= 500) return "Ошибка на стороне сервиса. Попробуйте позже.";
    return "Не удалось выполнить запрос (код " + code + ").";
  }
  async function request(url, opts, signal) {
    let resp;
    try { resp = await fetch(url, Object.assign({}, opts, { signal })); }
    catch (e) {
      if (e && e.name === "AbortError") throw e;
      throw new Error("Сервис недоступен. Проверьте подключение и попробуйте ещё раз.");
    }
    if (!resp.ok) { const e = new Error(statusMessage(resp.status)); e.http = resp.status; throw e; }
    try { return await resp.json(); }
    catch (_) { throw new Error("Сервис вернул неожиданный ответ."); }
  }
  function failMessage(data) {
    const m = data && (data.error || data.message || data.detail);
    return typeof m === "string" && m.trim() ? "Не удалось обработать видео: " + m : "Сервис не смог обработать это видео. Попробуйте другой файл.";
  }

  /* ---------- анализ: реальный API ---------- */
  async function analyzeApi(f, r) {
    setStatus("Загружаем видео…");
    const fd = new FormData(); fd.append("file", f);
    const created = await request(apiUrl("/api/v1/analyze"), { method: "POST", headers: authHeaders(), body: fd }, r.signal);
    if (!created || !created.job_id) throw new Error("Сервис не вернул идентификатор задачи.");
    setStatus("Анализируем видео…");
    let fails = 0;
    for (;;) {
      await sleep(C.POLL_MS, r.signal);
      let data;
      try {
        data = await request(apiUrl("/api/v1/jobs/" + encodeURIComponent(created.job_id)), { headers: authHeaders() }, r.signal);
        fails = 0;
      } catch (e) {
        if (e.name === "AbortError") throw e;
        if (e.http === 401 || e.http === 403 || e.http === 404 || ++fails >= 3) throw e; // разовые сбои сети терпим
        continue;
      }
      if (data.status === "done") return data;
      if (data.status === "failed") throw new Error(failMessage(data));
    }
  }

  /* ---------- анализ: демо без бэка ---------- */
  async function analyzeDemo(f, r) {
    setStatus("Загружаем видео…");
    await sleep(1200, r.signal);
    setStatus("Анализируем видео…");
    await sleep(2000, r.signal);
    if (/fail/i.test(f.name)) throw new Error(failMessage({ error: "демо-ошибка (в имени файла есть «fail»)" }));
    if (/clean/i.test(f.name)) return { job_id: "7b10d4aa", status: "done", verdict: "no_violation", violations: [] };
    return JSON.parse(JSON.stringify(window.DEMO_DATA));
  }

  /* ---------- запуск ---------- */
  function startTimer(r) {
    const tick = () => {
      const s = Math.floor((Date.now() - r.t0) / 1000);
      timerEl.textContent = String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
    };
    tick(); r.tick = setInterval(tick, 500);
  }
  function finish(r) {
    clearInterval(r.tick); clearTimeout(r.limit);
    if (run === r) run = null;
    check.disabled = !file;
  }

  async function start() {
    if (!file || run) return;
    const f = file;
    const r = { controller: new AbortController(), t0: Date.now(), timedOut: false };
    r.signal = r.controller.signal;
    r.limit = setTimeout(() => { r.timedOut = true; r.controller.abort(); }, C.TIMEOUT_MS);
    run = r;
    showError(""); check.disabled = true; show("processing"); startTimer(r);
    try {
      const data = await (C.DEMO ? analyzeDemo(f, r) : analyzeApi(f, r));
      const list = Array.isArray(data.violations) ? data.violations : [];
      data.violations = list;
      if (!data.verdict) data.verdict = list.length ? "violation" : "no_violation";
      if (list.length) {
        setStatus("Готовим кадры…");
        try { await window.captureFrames(f, list, !!C.DEMO); } catch (_) {}
      }
      const wasCancelled = r.signal.aborted;
      finish(r);
      if (wasCancelled) return;
      try {
        window.initPlayer(f, $("player"), list);
        window.renderResult(data, $("result"));
      } catch (e) {
        console.error(e);
        show("upload"); showError("Не удалось отобразить результат. Попробуйте ещё раз.");
        return;
      }
      show("result");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      finish(r);
      if (e && e.name === "AbortError" && !r.timedOut) return; // пользователь нажал «Отменить»
      show("upload");
      showError(r.timedOut
        ? "Обработка заняла больше 5 минут, видео считается необработанным. Попробуйте ещё раз."
        : (e && e.message) || "Произошла неизвестная ошибка.");
    }
  }

  check.addEventListener("click", start);
  $("cancel").addEventListener("click", () => { if (run) run.controller.abort(); show("upload"); });
  $("again").addEventListener("click", () => {
    input.value = ""; setFile(null); show("upload"); window.scrollTo({ top: 0, behavior: "smooth" });
  });
})();

/* ================= 5. ДЕМО-ДАННЫЕ (нужны только при DEMO: true; на проде этот раздел можно удалить) ================= */
window.DEMO_DATA = {"job_id": "3f2a9c1e", "status": "done", "verdict": "violation", "violations": [{"type": "Пример: нарушение 1", "timecode_sec": 11.0, "description": "Пример описания: что видно на видео и почему это может быть нарушением.", "rule": {"document": "Пример документа (НЕ реальный)", "point": "п. X", "text": "Пример формулировки правила (замените на проверенную цитату из акта РК).", "url": ""}, "evidence_frames": [{"timestamp_sec": 10.5, "image_base64": "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GKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXH4oxT8UYpGdxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuPxRinYoxQRcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLn/9k="}, {"timestamp_sec": 11.0, "image_base64": "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UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4zFGKfijFAXGYoxT8UYoC4/FGKfijFIzuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFxmKMU/FGKAuMxRin4oxQFx+KMU7FGKCLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBcbijFOxRigLjcUYp2KMUBc/9k="}]}]};
