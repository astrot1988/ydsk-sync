#!/bin/sh
set -eu

CONFIG_FILE="${YANDEX_DISK_CONFIG_DIR}/config.cfg"
AUTH_FILE="${YANDEX_DISK_AUTH_FILE:-${YANDEX_DISK_CONFIG_DIR}/passwd}"
PROXY_VALUE="${YANDEX_DISK_PROXY:-auto}"
EXCLUDE_DIRS_VALUE="${YANDEX_DISK_EXCLUDE_DIRS:-}"

mkdir -p "${YANDEX_DISK_CONFIG_DIR}" "${YANDEX_DISK_DATA_DIR}"

write_config() {
  cat >"${CONFIG_FILE}" <<EOF
auth="${AUTH_FILE}"
dir="${YANDEX_DISK_DATA_DIR}"
EOF

  if [ -n "${EXCLUDE_DIRS_VALUE}" ]; then
    printf 'exclude-dirs="%s"\n' "${EXCLUDE_DIRS_VALUE}" >>"${CONFIG_FILE}"
  fi

  if [ -n "${PROXY_VALUE}" ]; then
    printf 'proxy=%s\n' "${PROXY_VALUE}" >>"${CONFIG_FILE}"
  fi
}

run_yandex_disk() {
  if [ -n "${YDSK_REMOTE_PATH:-}" ]; then
    echo "YDSK_REMOTE_PATH is not supported by the official yandex-disk client; it syncs the whole directory mapped to /data" >&2
    exit 1
  fi

  write_config

  if [ ! -f "${AUTH_FILE}" ]; then
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_ADMIN:-}" ]; then
      YANDEX_DISK_AUTH_FILE="${AUTH_FILE}" \
      YANDEX_DISK_CONFIG_FILE="${CONFIG_FILE}" \
      YANDEX_DISK_DATA_DIR="${YANDEX_DISK_DATA_DIR}" \
      /usr/local/bin/telegram_auth.py
    else
      echo "OAuth token file not found: ${AUTH_FILE}" >&2
      echo "Run 'token' manually or set TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN for Telegram-based authorization" >&2
      exit 1
    fi
  fi

  exec yandex-disk \
    --config="${CONFIG_FILE}" \
    --dir="${YANDEX_DISK_DATA_DIR}" \
    --auth="${AUTH_FILE}" \
    "$@"
}

if [ "$#" -eq 0 ]; then
  set -- sync
fi

case "$1" in
  shell)
    shift
    exec /bin/sh "$@"
    ;;
  token)
    shift
    write_config
    exec yandex-disk \
      --config="${CONFIG_FILE}" \
      --dir="${YANDEX_DISK_DATA_DIR}" \
      --auth="${AUTH_FILE}" \
      token "${AUTH_FILE}" "$@"
    ;;
  setup)
    shift
    exec yandex-disk \
      --config="${CONFIG_FILE}" \
      --dir="${YANDEX_DISK_DATA_DIR}" \
      --auth="${AUTH_FILE}" \
      setup "$@"
    ;;
  *)
    run_yandex_disk "$@"
    ;;
esac
