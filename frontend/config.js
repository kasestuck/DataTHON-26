/* config.js — настройки. Единственный файл, который нужно менять при подключении к бэку.

   API, который вызывает сайт (раздел 10 ТЗ):
     POST /api/v1/analyze        форма, поле "file" (.mov)  ->  { "job_id": "..." }
     GET  /api/v1/jobs/{job_id}  ->  { "status": "processing" | "done" | "failed", ... }
   При "done": { verdict: "violation"|"no_violation", violations: [{ type, timecode_sec, description,
     evidence_frames: [{ timestamp_sec, image_base64 }], rule?: "строка" | { document, point, text, url } }] }
   При "failed" сайт показывает текст из полей error / message / detail, если они есть.
   Коды: 401/403 неверный ключ, 400/415/422 неверный файл, 413 слишком большой, 429 и 5xx сервис занят.
   Если сайт и API на разных адресах, на бэке нужен CORS. Ключ X-API-Key лучше добавлять на сервере, не здесь. */
window.CONFIG = {
  DEMO: true,          // true: демо-ответ без бэка (данные в конце app.js) (имя файла с "clean" — без нарушений, с "fail" — ошибка)
  API_URL: "",         // "" = тот же адрес, что и сайт; иначе, например, "https://team.example.kz"
  API_KEY: "",         // оставить пустым, если ключ добавляет сервер
  POLL_MS: 2500,       // как часто спрашивать статус задачи
  MAX_MB: 100,         // максимальный размер файла
  TIMEOUT_MS: 5 * 60 * 1000  // 5 минут на обработку (требование ТЗ)
};
