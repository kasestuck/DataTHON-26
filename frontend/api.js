/* Same-origin browser transport. The server API key never enters the browser. */
(function () {
  "use strict";
  function statusMessage(code) {
    if (code === 401 || code === 403) return "Сессия недоступна. Обновите страницу и попробуйте снова.";
    if (code === 413) return "Файл слишком большой для сервиса (максимум 100 МиБ).";
    if ([400, 415, 422].includes(code)) return "Сервис не принял файл. Нужен непустой MOV-файл с корректным заголовком.";
    if (code === 404) return "Задача не найдена. Попробуйте загрузить видео ещё раз.";
    if (code === 429) return "Сервис перегружен. Попробуйте позже.";
    return "Ошибка сервиса. Попробуйте позже.";
  }
  async function jsonRequest(path, options, signal) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    signal?.addEventListener("abort", abort, { once: true });
    // This bounds one HTTP request, never the lifetime of the queued AI task.
    const timer = setTimeout(abort, options.method === "POST" ? 60000 : 15000);
    try {
      const response = await fetch(path, { ...options, credentials: "same-origin", signal: controller.signal });
      if (!response.ok) {
        const error = new Error(statusMessage(response.status)); error.http = response.status; throw error;
      }
      try { return await response.json(); }
      catch (_) { throw new Error("Сервис вернул неожиданный ответ."); }
    } catch (error) {
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      if (error.http) throw error;
      throw new Error("Сервис недоступен или не ответил вовремя. Проверьте подключение и повторите попытку.");
    } finally {
      clearTimeout(timer); signal?.removeEventListener("abort", abort);
    }
  }
  window.createBrowserApi = function () {
    let token = null;
    async function session(signal) {
      const data = await jsonRequest("/ui-api/v1/session", {}, signal);
      if (typeof data.csrf_token !== "string" || !data.csrf_token) throw new Error("Не удалось открыть сессию.");
      token = data.csrf_token;
    }
    return {
      async request(path, options, signal) {
        if (!token) await session(signal);
        const send = () => jsonRequest("/ui-api/v1" + path, {
          ...options, headers: { "X-CSRF-Token": token }
        }, signal);
        try { return await send(); }
        catch (error) {
          if (error.http !== 403) throw error;
          await session(signal);
          return send(); // Retry only an authorization rejection, never an uncertain upload.
        }
      }
    };
  };
  window.jpegDataUrl = function (value) {
    if (typeof value !== "string" || !/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length % 4) return null;
    try {
      const bytes = atob(value);
      if (!bytes.startsWith("\xff\xd8") || !bytes.endsWith("\xff\xd9")) return null;
      return "data:image/jpeg;base64," + value;
    } catch (_) { return null; }
  };
  window.validateAnalysisResult = function (data) {
    const invalid = () => { throw new Error("Сервис вернул некорректный результат анализа."); };
    if (!data || !["violation", "no_violation"].includes(data.verdict) || !Array.isArray(data.violations)) invalid();
    if ((data.verdict === "violation") !== (data.violations.length > 0)) invalid();
    for (const item of data.violations) {
      if (!item || typeof item.type !== "string" || !item.type.trim() || typeof item.description !== "string" ||
          !Number.isFinite(item.timecode_sec) || item.timecode_sec < 0 || !Array.isArray(item.evidence_frames)) invalid();
    }
    return data;
  };
})();
