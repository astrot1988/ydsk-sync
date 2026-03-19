#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_ID = os.environ.get("TELEGRAM_ADMIN", "").strip()
AUTH_FILE = os.environ["YANDEX_DISK_AUTH_FILE"]
CONFIG_FILE = os.environ["YANDEX_DISK_CONFIG_FILE"]
DATA_DIR = os.environ["YANDEX_DISK_DATA_DIR"]
TIMEOUT = int(os.environ.get("TELEGRAM_AUTH_TIMEOUT", "900"))


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


def wait_for_auth_command():
    deadline = time.time() + TIMEOUT
    offset = get_offset()

    send(
        "Yandex Disk auth file is missing.\n"
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


def run_token_flow():
    cmd = [
        "yandex-disk",
        "--config",
        CONFIG_FILE,
        "--dir",
        DATA_DIR,
        "--auth",
        AUTH_FILE,
        "token",
        AUTH_FILE,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    first_line = ""
    start = time.time()
    while time.time() - start < 30:
        line = proc.stdout.readline()
        if line:
            first_line = line.strip()
            break
        if proc.poll() is not None:
            break

    if first_line:
        send(
            "Open https://ya.ru/device with the required Yandex account and enter the code below within 300 seconds.\n\n"
            f"{first_line}"
        )
    else:
        send("Failed to obtain Yandex authorization code from yandex-disk token command.")

    output = first_line
    remainder = proc.communicate()[0].strip()
    if remainder:
        output = f"{output}\n{remainder}".strip()

    if proc.returncode != 0:
        raise RuntimeError(output or "yandex-disk token command failed")

    send("Yandex Disk authorization completed successfully.")


def main():
    if not BOT_TOKEN or not ADMIN_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN are required")

    wait_for_auth_command()
    run_token_flow()


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as exc:
        print(f"telegram connectivity error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        message = str(exc)
        if BOT_TOKEN and ADMIN_ID:
            try:
                send(f"Yandex Disk authorization failed: {message}")
            except Exception:
                pass
        print(message, file=sys.stderr)
        sys.exit(1)
