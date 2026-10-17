# Публикация через туннель к ноутбуку Back 1

Этот файл описывает подготовку. Команды создания туннеля и публикации ещё не выполнялись. API слушает `127.0.0.1:8000` на ноутбуке; наружный адрес выдаёт выбранный туннель. Секретный `API_KEY`, `.env` и учётные данные туннеля не помещайте в Git.

Для стабильного адреса можно использовать именованный Cloudflare Tunnel с собственным доменом. [Официальная инструкция по созданию туннеля](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/) описывает вход, создание, DNS-маршрут и запуск. [Формат конфигурации](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/) требует завершающее правило `http_status:404`; пример лежит в `deploy/cloudflared.example.yml`. Быстрый случайный адрес подходит лишь для предварительной проверки, не для устойчивого адреса жюри: [Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).

Перед публикацией на ноутбуке Back 1:

1. Проверить локальный `/health`, загрузку `.mov` около 29 МБ и обработку нескольких роликов. Убедиться, что `ANALYZER_TARGET=backend.analyzer:analyze_video`, каталог типов и проверенные правила на месте, а не включена заглушка.
2. Установить `cloudflared` на ноутбуке. Создать именованный туннель и DNS-маршрут по официальной инструкции. Для стабильного адреса нужны доступ к домену и учётная запись провайдера.
3. Скопировать `deploy/cloudflared.example.yml` в локальный файл вне Git, заменить UUID, путь к выданному файлу учётных данных и hostname. Проверить правила командой `cloudflared tunnel ingress validate` с выбранным конфигурационным файлом.
4. Установить `PUBLIC_ORIGIN=https://<PUBLIC_HOSTNAME>` для защищённой cookie браузерной сессии. Запустить API с одним воркером и затем туннель. Не настраивать проброс портов роутера: туннель подключается к локальному `127.0.0.1:8000`.
5. Из внешней сети проверить `/health`, отказ без `X-API-Key`, загрузку настоящего `.mov`, опрос `job_id` и время до `done`/`failed`. Передать жюри только итоговый HTTPS URL, адрес API и ключ по согласованному каналу.

Туннельный провайдер участвует в передаче загружаемого видео. До использования тестовых видео согласуйте с командой, что выбранный путь соответствует условиям кейса о данных. Ни один тестовый ролик не должен использоваться для обучения или публиковаться.

Для Windows 11 с RTX 4060 контейнерный GPU-доступ требует Docker Desktop с WSL 2 и поддерживаемым драйвером NVIDIA; это описано в [Docker GPU guide](https://docs.docker.com/desktop/features/gpu/). `compose.yaml` резервирует GPU по [Docker Compose GPU guide](https://docs.docker.com/compose/how-tos/gpu-support/). На текущем компьютере Back 2 Docker не установлен, поэтому сборка и проверка GPU выполняются на ноутбуке Back 1 после интеграции модели.
