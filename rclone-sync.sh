#!/bin/sh
set -eu

REMOTE_NAME="${1:?remote name is required}"
REMOTE_PATH="${2:?remote path is required}"
LOCK_FILE="${3:-/tmp/rclone-sync.lock}"
RCLONE_SYNC_FLAGS="${4:-}"

shift 4 || true

if mkdir "${LOCK_FILE}" 2>/dev/null; then
  trap 'rmdir "${LOCK_FILE}"' EXIT INT TERM
else
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] sync already running, skipping event" >&2
  exit 0
fi

echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] lsyncd triggered sync" >&2
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] running: rclone sync /data ${REMOTE_NAME}:${REMOTE_PATH} ${RCLONE_SYNC_FLAGS}" >&2

if [ -n "${RCLONE_SYNC_FLAGS}" ]; then
  # shellcheck disable=SC2086
  set -- $RCLONE_SYNC_FLAGS "$@"
fi

rclone sync /data "${REMOTE_NAME}:${REMOTE_PATH}" "$@"
rc=$?
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] rclone sync finished with exit code ${rc}" >&2
exit "${rc}"
