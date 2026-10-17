/* app.js — весь JavaScript сайта в одном файле. Разделы:
   1) ШАПКА И ФОН (капли, меню профиля, заставка) — на всех страницах
   2) РЕЗУЛЬТАТ: renderResult(data, root) — вердикт, карточки, кадры, окно правила
   3) ПЛЕЕР: initPlayer(file, root, violations), seekTo(sec)
   4) ЗАГРУЗКА И API: выбор файла, запросы, опрос, таймер, ошибки (только на analyze.html)
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
    const name = ((function () { try { return localStorage.getItem("fv_name"); } catch (e) { return null; } })() || "Специалист").trim() || "Специалист";
    const link = (href, t) => `<a href="${href}" class="${page === href ? "is-active" : ""}">${t}</a>`;
    host.className = "glass header";
    host.innerHTML = `
      <a class="logo" href="index.html"><span class="logo-drop"></span>
        <span><b>Forte Vision</b> <small>ИИ-помощник для проверки кассовой дисциплины</small></span></a>
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
    no_violation: "Признаки нарушений не обнаружены",
    violation: "Обнаружены признаки возможного нарушения",
  };
  const NOTICE = "Результаты автоматического анализа носят предварительный характер и требуют проверки специалистом.";

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text; // textContent: защита от HTML-вставки
    return n;
  }
  function fmt(sec) {
    const tenths = Math.round(Math.max(0, Number(sec) || 0) * 10);
    return String(Math.floor(tenths / 600)).padStart(2, "0") + ":" + ((tenths % 600) / 10).toFixed(1).padStart(4, "0");
  }
  function ruleText(r) {
    if (!r || r.status !== "verified") return null;
    if (typeof r === "string") return r;
    return [r.document, r.point].filter(Boolean).join(", ") || null;
  }
  function openLightbox(src) {
    const box = el("div", "lightbox");
    const img = el("img"); img.src = src; img.alt = "Кадр события";
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
    box.append(x, el("h3", null, v.type || "Событие"), el("p", "muted", "Нормативное основание"));
    box.append(el("p", null, [rule.document, rule.point].filter(Boolean).join(" — ") || "Нормативное основание требует дополнительной проверки."));
    if (rule.text) box.append(el("blockquote", null, rule.text));
    if (rule.url && /^https:\/\//.test(rule.url)) {
      const a = el("a", null, "Открыть документ"); a.href = rule.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      box.append(a);
    }
    box.append(el("p", "src", "Результаты автоматического анализа носят предварительный характер и требуют проверки специалистом."));
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
    const title = el("h3", null, (total > 1 ? (i + 1) + ". " : "") + (v.type || "Событие"));
    const seek = el("button", "btn btn--ghost btn--small", "Перейти к " + fmt(v.timecode_sec));
    seek.type = "button";
    seek.title = "Перейти к моменту события";
    seek.addEventListener("click", () => {
      if (window.seekTo) window.seekTo(v.timecode_sec);
      const p = document.querySelector(".player");
      if (p) p.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    head.append(title, seek);
    c.append(head, el("p", null, v.description || ""));

    const frames = el("div", "frames");
    (v.evidence_frames || []).slice(0, 3).forEach((f) => {
      const src = window.jpegDataUrl(f?.image_base64);
      if (!src) { frames.append(el("span", "muted", "Кадр недоступен")); return; }
      const b = el("button", "frame"); b.type = "button";
      b.setAttribute("aria-label", "Увеличить кадр " + fmt(f.timestamp_sec));
      const img = el("img"); img.alt = "Кадр на " + fmt(f.timestamp_sec);
      img.addEventListener("error", () => b.replaceWith(el("span", "muted", "Кадр недоступен")), { once: true });
      img.src = src;
      b.append(img, el("span", null, fmt(f.timestamp_sec)));
      b.addEventListener("click", () => openLightbox(src));
      frames.append(b);
    });
    if (frames.children.length) c.append(frames);

    const rule = el("div", "rule");
    rule.append(el("b", null, "Нормативное основание"),
      el("span", null, ruleText(v.rule)));
    if (ruleText(v.rule)) {
      const more = el("button", "btn btn--ghost btn--small", "Подробнее об основании");
      more.type = "button"; more.addEventListener("click", () => openModal(v));
      rule.append(more);
    }
    if (ruleText(v.rule)) c.append(rule);
    return c;
  }

  window.renderResult = function (data, root) {
    root.replaceChildren();
    const list = [...data.violations].sort((a, b) => a.timecode_sec - b.timecode_sec);
    const bad = data.verdict === "violation";
    const head = el("section", "glass verdict " + (bad ? "verdict--bad" : "verdict--ok"));
    head.setAttribute("role", "status");
    const left = el("div");
    left.append(el("div", "eyebrow", "Результат проверки"), el("h2", null, TEXT[bad ? "violation" : "no_violation"]));
    head.append(left);
    if (list.length > 1) { // счётчик нужен только если нарушений несколько
      const cnt = el("div", "counter", String(list.length)); cnt.title = "Количество обнаруженных событий"; head.append(cnt);
    }
    root.append(head);

    const wrap = el("div", "results");
    list.forEach((v, i) => wrap.append(card(v, i, list.length)));
    root.append(wrap, el("p", "notice", NOTICE));
    if (!bad) root.append(el("p", "notice", "Отсутствие обнаруженных признаков не гарантирует отсутствия нарушений."));
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

  window.disposePlayer = function () {
    if (video) { video.pause(); video.removeAttribute("src"); video.load(); }
    if (url) URL.revokeObjectURL(url);
    url = null; video = null;
  };
  window.addEventListener("pagehide", window.disposePlayer);

  window.initPlayer = function (file, root, violations) {
    window.disposePlayer();
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
      return stub("Браузер не поддерживает воспроизведение этого видеофайла. Время событий и кадры доступны в результатах анализа ниже.");
    }
    url = URL.createObjectURL(file);
    video = probe;
    video.src = url;
    video.controls = true;
    video.preload = "metadata";
    video.setAttribute("aria-label", "Загруженное видео");
    video.addEventListener("error", () =>
      stub("Не удалось воспроизвести видеозапись в браузере. Результаты анализа доступны ниже."));

    const bar = document.createElement("div");
    bar.className = "timeline";
    bar.setAttribute("aria-label", "Шкала времени с отметками событий");
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
        m.title = (i + 1) + ". " + (v.type || "Событие") + " — " + v.timecode_sec + " с";
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

})();

/* ================= 4. ЗАГРУЗКА И API ================= */
/* app.js — выбор файла, проверка, запросы к API, опрос статуса, таймер, отмена, ошибки.
   Вызывает: initPlayer(file, root, violations) из player.js и renderResult(data, root) из result.js.
   Конфиг — config.js. Запросы идут только к локальному серверу сайта. */
(function () {
  "use strict";
  if (!document.getElementById("screen-upload")) return; // about.html и profile.html: только шапка и фон
  const C = Object.assign({ POLL_MS: 2500, MAX_MB: 100 }, window.CONFIG || {});
  const api = window.createBrowserApi();
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
      if (!/\.mov$/i.test(f.name)) showError("Выберите видеофайл в формате MOV.");
      else if (f.size > C.MAX_MB * 1024 * 1024) showError("Размер файла превышает допустимый: " + C.MAX_MB + " МиБ");
      else if (f.size === 0) showError("Выбранный файл пуст. Выберите другую видеозапись.");
      else file = f;
    }
    fname.textContent = file ? file.name + " · " + (file.size / 1048576).toFixed(1) + " МиБ" : "";
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
  function sleep(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal.aborted) return reject(new DOMException("Aborted", "AbortError"));
      const abort = () => { clearTimeout(t); reject(new DOMException("Aborted", "AbortError")); };
      const t = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
      signal.addEventListener("abort", abort, { once: true });
    });
  }

  /* ---------- анализ: реальный API ---------- */
  async function analyzeApi(f, r) {
    setStatus("Загрузка видеозаписи…");
    const fd = new FormData(); fd.append("file", f);
    const created = await api.request("/analyze", { method: "POST", body: fd }, r.signal);
    if (!created || !created.job_id) throw new Error("Не удалось запустить анализ видеозаписи. Повторите попытку позже.");
    setStatus("Выполняется анализ видеозаписи. Это может занять несколько минут.");
    let fails = 0;
    for (;;) {
      await sleep(C.POLL_MS, r.signal);
      let data;
      try {
        data = await api.request("/jobs/" + encodeURIComponent(created.job_id), {}, r.signal);
        fails = 0;
      } catch (e) {
        if (e.name === "AbortError") throw e;
        if (e.http === 401 || e.http === 403 || e.http === 404 || ++fails >= 3) throw e; // разовые сбои сети терпим
        continue;
      }
      if (data.status === "done") return window.validateAnalysisResult(data);
      if (data.status === "failed") throw new Error("Не удалось завершить анализ видеозаписи. Повторите попытку позже.");
      if (data.status !== "processing") throw new Error("Не удалось получить статус анализа. Повторите попытку позже.");
    }
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
    clearInterval(r.tick);
    if (run === r) run = null;
    check.disabled = !file;
  }

  async function start() {
    if (!file || run) return;
    const f = file;
    const r = { controller: new AbortController(), t0: Date.now() };
    r.signal = r.controller.signal;
    run = r;
    showError(""); check.disabled = true; show("processing"); startTimer(r);
    try {
      const data = await analyzeApi(f, r);
      const list = data.violations;
      const wasCancelled = r.signal.aborted;
      finish(r);
      if (wasCancelled) return;
      try {
        window.initPlayer(f, $("player"), list);
        window.renderResult(data, $("result"));
      } catch (e) {
        show("upload"); showError("Не удалось отобразить результаты анализа. Повторите попытку позже.");
        return;
      }
      show("result");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      finish(r);
      if (e && e.name === "AbortError") return; // пользователь нажал «Отменить»
      show("upload");
      showError((e && e.message) || "Не удалось завершить анализ видеозаписи. Повторите попытку позже.");
    }
  }

  check.addEventListener("click", start);
  $("cancel").addEventListener("click", () => {
    if (run) run.controller.abort();
    show("upload"); showError("Ожидание остановлено. Обработка видеозаписи на сервере может продолжиться.");
  });
  window.addEventListener("pagehide", () => { if (run) run.controller.abort(); });
  $("again").addEventListener("click", () => {
    window.disposePlayer(); $("player").replaceChildren(); $("result").replaceChildren();
    input.value = ""; setFile(null); show("upload"); window.scrollTo({ top: 0, behavior: "smooth" });
  });
})();
