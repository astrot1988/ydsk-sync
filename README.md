# ydsk-sync

Контейнер для синхронизации локального каталога `/data` в каталог на Yandex Disk через `rclone`.

В репозитории также есть отдельный новый Python-only вариант загрузчика, который работает без `rclone` и загружает файлы через Yandex Disk REST API.

## Статус

- `Dockerfile.rclone` - основной и поддерживаемый вариант.
- `Dockerfile.yandex-disk` - контейнер с официальным `yandex-disk` пока находится в разработке, использовать его не нужно.
- `Dockerfile.python-uploader` - отдельный Python-only uploader. Он не заменяет текущий `rclone`-сценарий и запускается через отдельный compose-файл.

## Готовый образ

Уже собранный образ публикуется в GitHub Container Registry:

```bash
ghcr.io/astrot1988/ydsk-sync:latest
```

Сейчас CI публикует Python uploader image. Также публикуются теги branch и `sha-<commit>`.

## Как работает

- контейнер делает начальный `rclone sync`
- затем `lsyncd` отслеживает изменения в `/data`
- при изменениях запускается `rclone sync` в `${RCLONE_REMOTE_NAME}:${YDSK_REMOTE_PATH}`
- дополнительно есть периодический fallback-sync каждые `PERIODIC_SYNC_SECONDS`, чтобы изменения не терялись на окружениях без надёжного inotify

## Запуск через Docker Compose

```bash
cp .env.example .env

docker compose up -d ydsk-rclone
docker compose logs -f ydsk-rclone
```

## Авторизация

Если `rclone` remote ещё не настроен, контейнер может провести авторизацию через Telegram и Yandex Device Flow.

Нужны переменные:

- `YANDEX_CLIENT_ID`
- `YANDEX_CLIENT_SECRET`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN`

Флоу:

1. контейнер видит, что remote отсутствует
2. отправляет запрос в Telegram
3. ждёт `/auth`
4. получает у Яндекса `device_code`
5. присылает ссылку `https://oauth.yandex.com/device` и код
6. пользователь вводит код в браузере
7. контейнер сам получает токены и пишет `rclone.conf`

## Основные переменные

- `RCLONE_REMOTE_NAME` - имя remote, по умолчанию `yd`
- `YDSK_REMOTE_PATH` - целевой каталог на Yandex Disk
- `LSYNCD_DELAY_SECONDS` - debounce перед sync, по умолчанию `5`
- `LSYNCD_LOG_LEVEL` - уровень логирования `lsyncd`
- `PERIODIC_SYNC_SECONDS` - резервный периодический sync, по умолчанию `300`
- `RCLONE_SYNC_FLAGS` - дополнительные флаги для `rclone sync`
- `YANDEX_OAUTH_DEBUG` - `1`, чтобы печатать debug OAuth в логи

## Локальная сборка

```bash
docker build -f ./Dockerfile.rclone -t ydsk-rclone .
```

## Python uploader

Этот вариант запускается отдельно и не затрагивает текущий `rclone`-стек.

Что он делает:

- авторизуется через Telegram + Yandex Device Flow
- сравнивает локальное дерево `/data` с удалённым каталогом на Yandex Disk
- загружает только новые или изменившиеся файлы напрямую в Yandex Disk API
- удаляет на Yandex Disk файлы, которых больше нет локально
- перед загрузкой переименовывает локальный файл в `*.file`
- после успешной загрузки переименовывает удалённый файл из `*.file` в финальное имя
- после успешной загрузки возвращает локальному файлу исходное имя и оставляет его на диске

Быстрый старт:

```bash
cp .env.python-uploader.example .env.python-uploader

docker compose --env-file ./.env.python-uploader -f ./docker-compose.python-uploader.yml up --build -d
docker compose --env-file ./.env.python-uploader -f ./docker-compose.python-uploader.yml logs -f ydsk-python-uploader
```

Для нового сервиса используются отдельные каталоги:

- `./config-python`
- `./data-python`

## CI/CD

При коммите в `master` GitHub Actions собирает образ из `Dockerfile.python-uploader` и публикует его в GHCR.

## Источники

- [rclone Yandex Disk docs](https://rclone.org/yandex/)
- [Yandex OAuth device flow](https://yandex.com/dev/id/doc/en/codes/screen-code-oauth)
