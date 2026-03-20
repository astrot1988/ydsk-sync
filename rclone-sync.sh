#!/bin/sh
set -eu

REMOTE_NAME="${1:?remote name is required}"
REMOTE_PATH="${2:?remote path is required}"
LOCK_FILE="${3:-/tmp/rclone-sync.lock}"
RCLONE_SYNC_FLAGS="${4:-}"
PENDING_FILE="${LOCK_FILE}.pending"

shift 4 || true

run_once() {
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] lsyncd triggered sync" >&2
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] running: rclone sync /data ${REMOTE_NAME}:${REMOTE_PATH} ${RCLONE_SYNC_FLAGS}" >&2

  if [ -n "${RCLONE_SYNC_FLAGS}" ]; then
    # shellcheck disable=SC2086
    set -- $RCLONE_SYNC_FLAGS "$@"
  fi

  rclone sync /data "${REMOTE_NAME}:${REMOTE_PATH}" "$@"
  rc=$?
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] rclone sync finished with exit code ${rc}" >&2
  return "${rc}"
}

if mkdir "${LOCK_FILE}" 2>/dev/null; then
  trap 'rmdir "${LOCK_FILE}"; rm -f "${PENDING_FILE}"' EXIT INT TERM
else
  : > "${PENDING_FILE}"
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] sync already running, queued one more pass" >&2
  exit 0
fi

rc=0
while true; do
  rm -f "${PENDING_FILE}"
  if ! run_once "$@"; then
    rc=$?
  fi
  if [ ! -f "${PENDING_FILE}" ]; then
    break
  fi
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] pending changes detected, running sync again" >&2
done

exit "${rc}"
