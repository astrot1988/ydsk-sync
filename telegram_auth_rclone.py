#!/usr/bin/env python3
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_ID = os.environ.get("TELEGRAM_ADMIN", "").strip()
RCLONE_CONFIG = os.environ["RCLONE_CONFIG"]
REMOTE_NAME = os.environ.get("RCLONE_REMOTE_NAME", "yd").strip() or "yd"
YANDEX_CLIENT_ID = os.environ.get("YANDEX_CLIENT_ID", "").strip()
YANDEX_CLIENT_SECRET = os.environ.get("YANDEX_CLIENT_SECRET", "").strip()
YANDEX_OAUTH_SCOPE = os.environ.get("YANDEX_OAUTH_SCOPE", "").strip()
YANDEX_OAUTH_DEBUG = os.environ.get("YANDEX_OAUTH_DEBUG", "").strip() == "1"
TIMEOUT = int(os.environ.get("TELEGRAM_AUTH_TIMEOUT", "900"))
DEVICE_CODE_URL = "https://oauth.yandex.com/device/code"
TOKEN_URL = "https://oauth.yandex.com/token"
VERIFY_URL = "https://oauth.yandex.com/device"


def debug(message):
    if YANDEX_OAUTH_DEBUG:
        print(f"[yandex-oauth-debug] {message}", file=sys.stderr, flush=True)


def api(method, params=None):
    if params is None:
        params = {}
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=data,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode())
    if not payload.get("ok"):
        raise RuntimeError(f"telegram api error for {method}: {payload}")
    return payload["result"]


def send(text):
    api("sendMessage", {"chat_id": ADMIN_ID, "text": text})


def get_offset():
    updates = api("getUpdates", {"timeout": 0, "limit": 100})
    if not updates:
        return 0
    return updates[-1]["update_id"] + 1


def run(cmd):
    completed = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        env={**os.environ, "RCLONE_CONFIG": RCLONE_CONFIG},
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(message or f"command failed: {' '.join(cmd)}")
    return completed.stdout


def remote_exists():
    completed = subprocess.run(
        ["rclone", "config", "show", REMOTE_NAME],
        text=True,
        capture_output=True,
        env={**os.environ, "RCLONE_CONFIG": RCLONE_CONFIG},
    )
    return completed.returncode == 0 and bool(completed.stdout.strip())


def request_json(url, data, headers=None):
    encoded = urllib.parse.urlencode(data).encode()
    debug(f"HTTP POST {url} data={json.dumps(data, ensure_ascii=False)}")
    req = urllib.request.Request(url, data=encoded, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode())
            debug(f"HTTP {url} response={json.dumps(payload, ensure_ascii=False)}")
            return payload
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        debug(f"HTTP {url} error_status={exc.code} body={body}")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
        error = payload.get("error") or ""
        description = payload.get("error_description") or ""
        message = description or error or body
        err = RuntimeError(message)
        setattr(err, "oauth_error", error)
        setattr(err, "oauth_error_description", description)
        raise err from exc


def request_device_code():
    payload = {
        "client_id": YANDEX_CLIENT_ID,
        "device_id": str(uuid.uuid4()),
        "device_name": f"ydsk-rclone-{REMOTE_NAME}",
    }
    if YANDEX_OAUTH_SCOPE:
        payload["scope"] = YANDEX_OAUTH_SCOPE
    response = request_json(DEVICE_CODE_URL, payload)
    debug(
        "device_code issued "
        f"device_code={response.get('device_code')} "
        f"user_code={response.get('user_code')} "
        f"interval={response.get('interval')} "
        f"expires_in={response.get('expires_in')}"
    )
    return response


def poll_token(device_code, interval):
    deadline = time.time() + TIMEOUT
    auth = base64.b64encode(f"{YANDEX_CLIENT_ID}:{YANDEX_CLIENT_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        try:
            debug(f"poll_token attempt={attempt} device_code={device_code}")
            response = request_json(
                TOKEN_URL,
                {"grant_type": "device_code", "code": device_code},
                headers=headers,
            )
            debug(f"poll_token success attempt={attempt} response={json.dumps(response, ensure_ascii=False)}")
            return response
        except RuntimeError as exc:
            message = str(exc)
            oauth_error = getattr(exc, "oauth_error", "")
            oauth_description = getattr(exc, "oauth_error_description", "")
            debug(
                "poll_token error "
                f"attempt={attempt} "
                f"error={oauth_error or message} "
                f"description={oauth_description or message}"
            )
            if oauth_error in {"authorization_pending", "slow_down"}:
                time.sleep(interval + (5 if oauth_error == "slow_down" else 0))
                continue
            if message == "User has not yet authorized your application":
                time.sleep(interval)
                continue
            raise
    raise RuntimeError("timeout waiting for Yandex OAuth confirmation")


def wait_for_auth_command():
    deadline = time.time() + TIMEOUT
    offset = get_offset()

    send(
        "Rclone config for Yandex Disk is missing.\n"
        "Send /auth to start authorization or /cancel to abort."
    )

    while time.time() < deadline:
        updates = api("getUpdates", {"timeout": 30, "offset": offset})
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            if str(chat.get("id")) != ADMIN_ID:
                continue
            text = (message.get("text") or "").strip().lower()
            if text == "/cancel":
                raise RuntimeError("authorization cancelled from telegram")
            if text == "/auth":
                return
    raise RuntimeError("timeout waiting for /auth in telegram")


def create_remote(token_response):
    debug(f"create_remote start remote={REMOTE_NAME}")
    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(token_response["expires_in"]))
    token_json = json.dumps(
        {
            "access_token": token_response["access_token"],
            "token_type": token_response.get("token_type", "bearer"),
            "refresh_token": token_response.get("refresh_token", ""),
            "expiry": expiry.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        },
        separators=(",", ":"),
    )
    run(
        [
            "rclone",
            "config",
            "create",
            "--non-interactive",
            REMOTE_NAME,
            "yandex",
            "client_id",
            YANDEX_CLIENT_ID,
            "client_secret",
            YANDEX_CLIENT_SECRET,
            "token",
            token_json,
        ]
    )
    debug(f"rclone remote created remote={REMOTE_NAME} config={RCLONE_CONFIG}")


def main():
    if not BOT_TOKEN or not ADMIN_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN are required")
    if not YANDEX_CLIENT_ID or not YANDEX_CLIENT_SECRET:
        raise RuntimeError("YANDEX_CLIENT_ID and YANDEX_CLIENT_SECRET are required")
    if remote_exists():
        return

    os.makedirs(os.path.dirname(RCLONE_CONFIG), exist_ok=True)
    wait_for_auth_command()
    device = request_device_code()

    send(
        "Open the link below in a browser logged into the required Yandex account and enter the confirmation code.\n\n"
        f"{VERIFY_URL}\n\n"
        f"Code: {device['user_code']}\n"
        f"Expires in: {device.get('expires_in', 0)} seconds"
    )

    token_response = poll_token(device["device_code"], int(device.get("interval", 5)))
    debug("token received from Yandex, proceeding to create_remote")
    create_remote(token_response)
    debug("create_remote finished, sending telegram success message")
    send(f"Rclone remote '{REMOTE_NAME}' configured successfully.")
    debug("telegram success message sent")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as exc:
        print(f"network error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        message = str(exc)
        if BOT_TOKEN and ADMIN_ID:
            try:
                send(f"Rclone authorization failed: {message}")
            except Exception:
                pass
        print(message, file=sys.stderr)
        sys.exit(1)
