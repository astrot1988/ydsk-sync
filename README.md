# Yandex Disk CLI in Docker

В каталоге есть два варианта:

- `Dockerfile.rclone` - контейнер с `rclone` для Yandex Disk.
- `Dockerfile.yandex-disk` - контейнер с официальным `yandex-disk`.

`rclone` практичнее для контейнера и разовых операций. `yandex-disk` ближе к классической модели синхронизации с демоном и локальной папкой.

## Сборка

`rclone`:

```bash
docker build -f /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/Dockerfile.rclone \
  -t ydsk-rclone \
  /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup
```

`yandex-disk`:

```bash
docker build --platform=linux/amd64 \
  -f /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/Dockerfile.yandex-disk \
  -t ydsk-yandex-disk \
  /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup
```

Примечание: официальный пакет Яндекса сейчас опубликован только для `amd64`, поэтому второй образ нужно собирать и запускать как `linux/amd64`.

## Rclone

Первичная настройка:

```bash
mkdir -p /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config

docker run --rm -it \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config:/config/rclone \
  ydsk-rclone config
```

В мастере `rclone`:

1. `n` - создать новый remote.
2. Имя, например `yd`.
3. Тип storage: `yandex`.
4. Завершить OAuth-авторизацию.

Основной режим работы:

- контейнер синхронизирует `/data` в удалённый путь из переменной `YDSK_REMOTE_PATH`
- remote по умолчанию `yd`
- имя remote можно переопределить через `RCLONE_REMOTE_NAME`
- по умолчанию контейнер делает начальный `sync`, затем `lsyncd` отслеживает изменения в `/data` и триггерит `rclone sync`

Запуск через `docker compose`:

```bash
cp /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/.env.example \
  /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/.env

docker compose -f /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/docker-compose.yml build
docker compose -f /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/docker-compose.yml up -d ydsk-rclone
```

Если `rclone` remote ещё не настроен, контейнер может инициировать авторизацию через Telegram-бота и прямой Yandex Device Flow. Для этого нужны:

- `YANDEX_CLIENT_ID` - OAuth client id вашего приложения в Яндексе
- `YANDEX_CLIENT_SECRET` - OAuth client secret вашего приложения в Яндексе
- `TELEGRAM_BOT_TOKEN` - токен бота
- `TELEGRAM_ADMIN` - Telegram user id администратора

Флоу такой:

1. контейнер обнаруживает отсутствие remote `RCLONE_REMOTE_NAME` в `rclone.conf`
2. отправляет сообщением в Telegram запрос на авторизацию
3. ждёт от `TELEGRAM_ADMIN` команду `/auth`
4. запрашивает у Яндекса device code
5. отправляет в Telegram ссылку [oauth.yandex.com/device](https://oauth.yandex.com/device) и пользовательский код
6. админ вводит код в браузере без установки `rclone`
7. контейнер сам получает access/refresh token и создаёт remote в `rclone.conf`
8. продолжает `sync /data -> ${YDSK_REMOTE_PATH}`

Синхронизация по умолчанию:

```bash
docker run --rm \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config:/config/rclone \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/data:/data \
  -e YDSK_REMOTE_PATH=backup/project-a \
  ydsk-rclone
```

Постоянная синхронизация через Compose:

```bash
docker compose -f /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/docker-compose.yml up -d ydsk-rclone
docker compose -f /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/docker-compose.yml logs -f ydsk-rclone
```

То же явно через команду `sync`:

```bash
docker run --rm \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config:/config/rclone \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/data:/data \
  -e YDSK_REMOTE_PATH=backup/project-a \
  ydsk-rclone sync
```

Если remote называется не `yd`:

```bash
docker run --rm \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config:/config/rclone \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/data:/data \
  -e RCLONE_REMOTE_NAME=mydisk \
  -e YDSK_REMOTE_PATH=backup/project-a \
  ydsk-rclone
```

Ручные команды по-прежнему доступны:

```bash
docker run --rm \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config:/config/rclone \
  ydsk-rclone lsd yd:
```

Прямой запуск Telegram flow:

```bash
docker run --rm \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config:/config/rclone \
  -e YANDEX_CLIENT_ID=... \
  -e YANDEX_CLIENT_SECRET=... \
  -e TELEGRAM_BOT_TOKEN=... \
  -e TELEGRAM_ADMIN=123456789 \
  ydsk-rclone auth-telegram
```

Переменные окружения для `rclone`:

- `RCLONE_REMOTE_NAME` - имя remote, по умолчанию `yd`
- `YDSK_REMOTE_PATH` - целевой каталог на Яндекс Диске
- `LSYNCD_DELAY_SECONDS` - debounce перед запуском sync после изменений, по умолчанию `5`
- `LSYNCD_LOG_LEVEL` - уровень логирования `lsyncd`
- `RCLONE_SYNC_FLAGS` - дополнительные флаги для `rclone sync`, например `-vv --dry-run`
- `YANDEX_CLIENT_ID` - OAuth client id для Device Flow
- `YANDEX_CLIENT_SECRET` - OAuth client secret для Device Flow
- `YANDEX_OAUTH_SCOPE` - необязательный scope для OAuth-запроса
- `YANDEX_OAUTH_DEBUG` - `1`, чтобы печатать в логи `device_code` и ответы `/token`
- `TELEGRAM_BOT_TOKEN` - токен Telegram-бота для автонастройки remote
- `TELEGRAM_ADMIN` - Telegram user id, которому бот пишет и от которого принимает `/auth`
- `TELEGRAM_AUTH_TIMEOUT` - сколько секунд ждать ответов, по умолчанию `900`

## Yandex Disk

Ограничение официального клиента: отдельный удалённый подкаталог типа `backup/project-a` он не принимает. Поддерживается только синхронизация всей локальной директории, переданной через `--dir`. В нашем контейнере это `/data`.

Если файла токена нет, контейнер может инициировать авторизацию через Telegram-бота. Для этого нужны:

- `TELEGRAM_BOT_TOKEN` - токен бота
- `TELEGRAM_ADMIN` - Telegram user id администратора

Флоу такой:

1. контейнер обнаруживает отсутствие файла `/config/yandex-disk/passwd`
2. отправляет сообщением в Telegram запрос на авторизацию
3. ждёт от `TELEGRAM_ADMIN` команду `/auth`
4. запускает `yandex-disk token`, получает device code
5. отправляет в Telegram ссылку и код для ввода на `https://ya.ru/device`
6. после успешной авторизации сохраняет токен и продолжает команду `sync` или `start`

Первичная настройка:

```bash
mkdir -p /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config-yandex
mkdir -p /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/data

docker run --rm -it --platform=linux/amd64 \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config-yandex:/config/yandex-disk \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/data:/data \
  ydsk-yandex-disk token
```

После этого токен будет лежать в `/Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config-yandex/passwd`.

Контейнер сам создаёт минимальный `config.cfg` со значениями:

- `auth="/config/yandex-disk/passwd"`
- `dir="/data"`
- опционально `proxy=...`
- опционально `exclude-dirs=...`

Разовая синхронизация:

```bash
docker run --rm --platform=linux/amd64 \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config-yandex:/config/yandex-disk \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/data:/data \
  ydsk-yandex-disk
```

То же через `docker compose`:

```bash
docker compose -f /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/docker-compose.yml build ydsk-yandex-disk
docker compose -f /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/docker-compose.yml run --rm ydsk-yandex-disk
```

Запуск демона:

```bash
docker run --rm -it --platform=linux/amd64 \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config-yandex:/config/yandex-disk \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/data:/data \
  ydsk-yandex-disk start
```

Статус:

```bash
docker run --rm --platform=linux/amd64 \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/config-yandex:/config/yandex-disk \
  -v /Users/aleksejlutovinov/Projects/quank-mvp/ydsk_backup/data:/data \
  ydsk-yandex-disk status
```

Переменные окружения для `yandex-disk`:

- `YANDEX_DISK_PROXY` - `auto`, `no` или ручная настройка proxy
- `YANDEX_DISK_EXCLUDE_DIRS` - список исключённых директорий через запятую
- `TELEGRAM_BOT_TOKEN` - токен Telegram-бота для автоавторизации
- `TELEGRAM_ADMIN` - Telegram user id, которому бот пишет и от которого принимает `/auth`
- `TELEGRAM_AUTH_TIMEOUT` - сколько секунд ждать `/auth`, по умолчанию `900`
- `YDSK_REMOTE_PATH` - не поддерживается официальным клиентом и вызовет ошибку

## Источники

- Официальная установка `yandex-disk`: <https://yandex.com/support/yandex-360/customers/disk/desktop/linux/en/installation>
- Официальные команды `yandex-disk`: <https://yandex.com/support/yandex-360/customers/disk/desktop/linux/en/cli-commands>
- Официальный пакет `yandex-disk` (`amd64`): <http://repo.yandex.ru/yandex-disk/deb/pool/main/y/yandex-disk/yandex-disk_0.1.6.1080_amd64.deb>
- Документация `rclone` для Yandex Disk: <https://rclone.org/yandex/>
