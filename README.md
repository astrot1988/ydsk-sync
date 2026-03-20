# ydsk-sync

Контейнер для синхронизации локального каталога `/data` в каталог на Yandex Disk через `rclone`.

## Статус

- `Dockerfile.rclone` - основной и поддерживаемый вариант.
- `Dockerfile.yandex-disk` - контейнер с официальным `yandex-disk` пока находится в разработке, использовать его не нужно.

## Готовый образ

Уже собранный образ публикуется в GitHub Container Registry:

```bash
ghcr.io/astrot1988/ydsk-sync:latest
```

Также публикуются теги branch и `sha-<commit>`.

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

## CI/CD

При коммите в `master` GitHub Actions собирает образ из `Dockerfile.rclone` и публикует его в GHCR.

## Источники

- [rclone Yandex Disk docs](https://rclone.org/yandex/)
- [Yandex OAuth device flow](https://yandex.com/dev/id/doc/en/codes/screen-code-oauth)
