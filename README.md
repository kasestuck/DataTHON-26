# ForteBank Datathon 26 — интегрированный backend

Инструкция Back 1 и контракт анализатора: [backend/analyzer/README.md](backend/analyzer/README.md).

FastAPI принимает `.mov`, ставит задания в последовательную очередь и вызывает локальный `backend.analyzer.analyze_video`. Реальный анализатор включён по умолчанию. Заглушка возвращает `[]` и доступна только при явном выборе для разработки API; она **не обнаруживает нарушения**.

## Нативный запуск Windows с локальными моделями

Запускайте команды из корня репозитория на Python 3.13.12. Веса размещаются отдельно в `models/rtdetr_r18vd` и `models/qwen3_vl_2b`; команды ниже не скачивают их.

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r backend/requirements-analyzer.txt
& .\.venv\Scripts\python.exe -m pip install torch==2.10.0+cu128 torchvision==0.25.0+cu128 --index-url https://download.pytorch.org/whl/cu128
& .\.venv\Scripts\python.exe -m pip install -r backend/requirements-models.txt
$env:API_KEY = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
$env:ANALYZER_TARGET = "backend.analyzer:analyze_video"
& .\.venv\Scripts\python.exe -m uvicorn backend.api.app:app_from_env --factory --host 127.0.0.1 --port 8000 --workers 1
```

`API_KEY` хранится только в окружении или приватном `.env`, не в Git и не в клиентском JavaScript. Uvicorn запускается одним процессом, очередь одним потоком. Back 2 ограничивает вызов 270 секундами, Back 1 — 260 секундами. При таймауте завершается и вложенный процесс анализатора, чтобы он не занимал GPU параллельно со следующим заданием.

## Локальный запуск без модели

Для запуска только API с заглушкой достаточно Python 3.10+. Совместный запуск с анализатором Back 1 ориентирован на проверенный ею Python 3.13.12. Скопируйте `.env.example` в локальный `.env` и задайте длинный случайный `API_KEY`. `.env` исключён из Git. При ручном запуске через PowerShell установите переменные окружения:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:API_KEY = "локальный-длинный-секретный-ключ"
$env:ANALYZER_TARGET = "backend.api.stub:analyze_video"
& .\.venv\Scripts\python.exe -m uvicorn backend.api.app:app_from_env --factory --host 127.0.0.1 --port 8000 --workers 1
```

Этот раздел — только для изолированной разработки API с явно выбранной заглушкой. Без `ANALYZER_TARGET` используется реальный `backend.analyzer:analyze_video`; отсутствие весов приводит к `failed`, а не к подмене анализа заглушкой. Оставьте один процесс Uvicorn (`--workers 1`).

## API и примеры

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/analyze -H "X-API-Key: локальный-длинный-секретный-ключ" -F "file=@video.mov"
curl.exe http://127.0.0.1:8000/api/v1/jobs/ПОЛУЧЕННЫЙ_JOB_ID -H "X-API-Key: локальный-длинный-секретный-ключ"
curl.exe http://127.0.0.1:8000/health
```

`POST /api/v1/analyze` принимает поле `file` и возвращает `job_id`. `GET /api/v1/jobs/{job_id}` возвращает `processing`, `done` или `failed`. При `done` ответ содержит `verdict` (`violation` или `no_violation`) и массив `violations`. Каждый элемент содержит только согласованные поля `type`, `timecode_sec`, `description`, `evidence_frames` (`timestamp_sec`, `image_base64`). При `failed` возвращается безопасное для клиента поле `error`; подробная причина остаётся в серверном журнале. Оба маршрута требуют `X-API-Key`; `/health` общедоступен.

Размер файла ограничен 100 МиБ, загрузка идёт по частям. Проверяются расширение `.mov` и заголовок контейнера; полную декодируемость видео должен проверить анализатор. Очевидно повреждённый файл отклоняется с 400, превышение лимита — с 413. Задания и статусы хранятся в SQLite (`var/jobs.sqlite3`). После завершения, ошибки или таймаута исходное видео удаляется. Незавершённые задания восстанавливаются после перезапуска. Задания обрабатываются по одному; таймаут анализатора — 270 секунд. Старые осиротевшие загрузки и завершённые записи удаляются после 24 часов, проверка выполняется при старте и раз в час.

## Контракт Back 1 и правила

Back 1 предоставляет `from backend.analyzer import analyze_video`, где `analyze_video(video_path: str) -> list[Finding]`. Каждый Finding: `violation_type: str`, `timecode_sec: float`, `description: str`, `confidence: float` в диапазоне 0–1, `evidence_frames: list[EvidenceFrame]` из 1–3 кадров. Кадр: `timestamp_sec: float`, `jpeg_bytes: bytes`. Сервис валидирует Finding, кодирует JPEG в base64 и формирует `verdict`. `confidence` в публичный JSON не входит. Некорректный результат или ошибка анализатора переводит задание в `failed`.

`backend/data/violation_types.json` имеет форму `{"type_id": {"name": "Название", "description": "Визуальные признаки"}}`: 18 визуальных категорий. Отдельная `violation_type_policy.json` разрешает автоматическую выдачу 12 категорий. В `rules.json` для всех ID стоит `needs_review`; нормативные основания не выдумываются. После ручных изменений каталога выполните:

```powershell
& .\.venv\Scripts\python.exe -m backend.api.rules sync
& .\.venv\Scripts\python.exe -m backend.api.rules check
```

`sync` создаёт для новых ID только `{"status": "needs_review"}`. Для ручного подтверждения запись меняют на `{"status": "verified", "document": "...", "section": "...", "url": "https://..."}`. Валидатор допускает лишь HTTPS-ссылки на `adilet.zan.kz` и его поддомены либо `nationalbank.kz` и его поддомены. Проверка домена **не подтверждает содержание документа или применимость нормы**: название, раздел и правовое утверждение нужно проверить вручную по официальному тексту. Пока статус не `verified`, источник в ответ не добавляется. Подтверждённый источник включается в `description`, не меняя обязательную схему API.

## Фронтенд

Front 1/2 могут положить готовую сборку в каталог и установить `FRONTEND_DIST` на его абсолютный путь (в Docker — путь внутри контейнера, например `/app/frontend/dist`, если сборка находится там до `docker compose up --build`). Тогда приложение раздаёт файлы с `/`. Ключ жюри не должен попадать в JavaScript: фронтенд получает `csrf_token` через `GET /ui-api/v1/session` и использует cookie-сессию с `X-CSRF-Token` для `POST /ui-api/v1/analyze` и `GET /ui-api/v1/jobs/{job_id}`. Cookie — HttpOnly, SameSite=Strict; при `PUBLIC_ORIGIN=https://...` также Secure. Эти маршруты доступны только при подключённой сборке. Публичный интерфейс допускает анонимные загрузки: перед реальной публикацией согласуйте с командой, нужен ли дополнительный контроль доступа или лимит запросов.

Браузерный интерфейс рассчитан на тот же origin, что и API: cookie с `SameSite=Strict` не предназначена для вызовов с другого сайта. Для разрешённых внешних клиентов API можно задать в `CORS_ORIGINS` явный список origin через запятую; `*` отклоняется. CORS сам по себе не передаёт клиенту API-ключ и не делает кросс-доменный браузерный интерфейс рабочим. Не размещайте `API_KEY` в публичных файлах.

Поле API `verdict=violation` означает найденный анализатором визуальный шаблон, **не доказанное юридическое нарушение**. Front 1/2 должны показывать пользователю «Обнаружены признаки возможного нарушения; требуется проверка человеком и нормативного основания». Для `no_violation` корректно «Поддерживаемые системой признаки не обнаружены», а не «Нарушений нет». При `failed` нельзя показывать отрицательный вердикт. До появления исходников фронтенда это инструкция для команды, а не уже внесённый текст интерфейса.

## Docker и публичный адрес

`Dockerfile` использует Python 3.13.12, API-зависимости DEV2, манифесты Back 1 и PyTorch 2.10.0/TorchVision 0.25.0 с CUDA 12.8. Полная ML-часть обязательна для интегрированного образа. Compose выбирает реальный анализатор, выделяет GPU, монтирует веса и три публичных JSON-каталога только для чтения. Приватные CSV/Excel, отчёты, JPEG и веса исключены из build-контекста; весь каталог приватной разметки внутрь контейнера не монтируется. Порт публикуется лишь на `127.0.0.1`. Для сборки нужны Docker Desktop с WSL 2 и поддерживаемый драйвер NVIDIA; см. [официальный Docker GPU guide](https://docs.docker.com/desktop/features/gpu/) и [Compose GPU guide](https://docs.docker.com/compose/how-tos/gpu-support/). Во время inference включены offline-флаги и сетевой запрет Back 1.

Скопируйте `.env.example` в `.env` и задайте случайный секрет. Локальные веса должны лежать в `models/rtdetr_r18vd/` и `models/qwen3_vl_2b/` рядом с `compose.yaml`; они не копируются в образ. Сначала выполните `docker compose build` и `docker compose run --rm api python -m backend.scripts.check_environment`, затем smoke моделей/API и только после этого `docker compose up`. Docker CLI на текущей Windows-машине отсутствует: сборка и GPU внутри контейнера не проверены. Нативная проверка Windows выполняется отдельно и не заменяет Docker-сборку.

План публикации через туннель к ноутбуку Back 1 без открытого входящего порта описан в [deploy/PUBLISHING.md](deploy/PUBLISHING.md). Туннель не создавался и сервис не публиковался.

## Тесты

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m ruff check backend tests
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -m backend.scripts.check_environment
& .\.venv\Scripts\python.exe -m backend.scripts.smoke_models --catalog-check
# Настоящий HTTP/Uvicorn + реальный MOV + локальные модели; вывод без кадров и ключей:
& .\.venv\Scripts\python.exe -m backend.scripts.smoke_api --video PATH_TO_LOCAL_VIDEO.mov --require-finding
```

Pytest собирает `backend/tests` и `tests`; 171 тест покрывает обе части, dataclass-контракт, декодируемый JPEG→base64, очередь, вложенный spawn, очистку дерева процессов по таймауту, закрытие SQLite-соединений, ошибки и браузерную интеграцию. Здесь используются явные синтетические компоненты. Отдельные CUDA/model/API smoke вызывают настоящие веса; `smoke_api` поднимает Uvicorn на случайном loopback-порту, отправляет реальное видео, проверяет JPEG из ответа, последовательность очереди, `failed` для повреждённого MOV и удаление временных загрузок. Секрет генерируется в памяти, исходник не изменяется, отчёт сохраняется только в игнорируемом `backend/data/reports/integration_BACK/`. Эти проверки не устанавливают качество детекции и юридическую обоснованность.

Объединение выполнено только локально в BACK из DEV1 `724fb77` и DEV2 `6a27bf6`.
Резервная ветка исходного BACK: `codex/backup-back-20261017-165903` → `5464901`.
DEV1, DEV2 и main сохранены. BACK не отправляется в origin без отдельного разрешения.
