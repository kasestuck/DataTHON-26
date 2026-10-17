/* Только публичные настройки. Запросы используют cookie/CSRF на том же origin. */
window.CONFIG = Object.freeze({ POLL_MS: 2500, MAX_MB: 100 });
