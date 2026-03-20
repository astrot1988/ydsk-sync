#!/bin/sh
set -eu

REMOTE_NAME="${RCLONE_REMOTE_NAME:-yd}"
REMOTE_PATH="${YDSK_REMOTE_PATH:-}"
LSYNCD_DELAY_SECONDS="${LSYNCD_DELAY_SECONDS:-5}"
LSYNCD_LOG_LEVEL="${LSYNCD_LOG_LEVEL:-normal}"
PERIODIC_SYNC_SECONDS="${PERIODIC_SYNC_SECONDS:-300}"
RCLONE_SYNC_FLAGS="${RCLONE_SYNC_FLAGS:-}"
LSYNCD_CONFIG_FILE="${LSYNCD_CONFIG_FILE:-/tmp/lsyncd.conf.lua}"
LOCK_FILE="${LOCK_FILE:-/tmp/rclone-sync.lock}"

ensure_remote_config() {
  mkdir -p "$(dirname "$RCLONE_CONFIG")"

  if rclone config show "${REMOTE_NAME}" >/dev/null 2>&1; then
    return 0
  fi

  if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_ADMIN:-}" ]; then
    RCLONE_REMOTE_NAME="${REMOTE_NAME}" \
    /usr/local/bin/telegram_auth_rclone.py
    return 0
  fi

  echo "Rclone remote '${REMOTE_NAME}' is not configured in ${RCLONE_CONFIG}" >&2
  echo "Run 'config' manually or set TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN for Telegram-based authorization" >&2
  exit 1
}

run_sync() {
  if [ -z "${REMOTE_PATH}" ]; then
    echo "YDSK_REMOTE_PATH is required for sync mode" >&2
    exit 1
  fi

  ensure_remote_config
  if [ -n "${RCLONE_SYNC_FLAGS}" ]; then
    # shellcheck disable=SC2086
    set -- $RCLONE_SYNC_FLAGS "$@"
  fi
  exec rclone sync /data "${REMOTE_NAME}:${REMOTE_PATH}" "$@"
}

run_sync_once() {
  if [ -z "${REMOTE_PATH}" ]; then
    echo "YDSK_REMOTE_PATH is required for sync mode" >&2
    exit 1
  fi

  ensure_remote_config
  if [ -n "${RCLONE_SYNC_FLAGS}" ]; then
    # shellcheck disable=SC2086
    set -- $RCLONE_SYNC_FLAGS "$@"
  fi
  rclone sync /data "${REMOTE_NAME}:${REMOTE_PATH}" "$@"
}

write_lsyncd_config() {
  if ! printf '%s' "${LSYNCD_DELAY_SECONDS}" | grep -Eq '^[0-9]+$'; then
    echo "LSYNCD_DELAY_SECONDS must be a non-negative integer" >&2
    exit 1
  fi

  cat >"${LSYNCD_CONFIG_FILE}" <<EOF
settings {
  nodaemon = true,
  logfile = "/dev/stderr",
  statusFile = "/tmp/lsyncd.status",
  statusInterval = 10,
}

sync {
  default.rsync,
  source = "/data",
  target = "/tmp/lsyncd-target-placeholder",
  delay = ${LSYNCD_DELAY_SECONDS},
  init = false,
  rsync = {
    binary = "/usr/local/bin/rclone-sync.sh",
    _extra = {"${REMOTE_NAME}", "${REMOTE_PATH}", "${LOCK_FILE}", "${RCLONE_SYNC_FLAGS}"},
  },
}
EOF
}

run_watch() {
  if [ -z "${REMOTE_PATH}" ]; then
    echo "YDSK_REMOTE_PATH is required for watch mode" >&2
    exit 1
  fi

  ensure_remote_config
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] initial sync start" >&2
  run_sync_once "$@"
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] initial sync complete, starting lsyncd" >&2
  if ! printf '%s' "${PERIODIC_SYNC_SECONDS}" | grep -Eq '^[0-9]+$'; then
    echo "PERIODIC_SYNC_SECONDS must be a non-negative integer" >&2
    exit 1
  fi
  if [ "${PERIODIC_SYNC_SECONDS}" -gt 0 ]; then
    (
      while true; do
        sleep "${PERIODIC_SYNC_SECONDS}"
        echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] periodic sync start" >&2
        run_sync_once "$@" || true
      done
    ) &
  fi
  write_lsyncd_config
  exec lsyncd "${LSYNCD_CONFIG_FILE}"
}

if [ "$#" -eq 0 ]; then
  run_watch
fi

case "$1" in
  shell)
    shift
    exec /bin/sh "$@"
    ;;
  config)
    shift
    mkdir -p "$(dirname "$RCLONE_CONFIG")"
    exec rclone config "$@"
    ;;
  auth-telegram)
    shift
    mkdir -p "$(dirname "$RCLONE_CONFIG")"
    RCLONE_REMOTE_NAME="${REMOTE_NAME}" \
    exec /usr/local/bin/telegram_auth_rclone.py "$@"
    ;;
  sync)
    shift
    run_sync "$@"
    ;;
  watch)
    shift
    run_watch "$@"
    ;;
  *)
    exec rclone "$@"
    ;;
esac
