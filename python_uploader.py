#!/usr/bin/env python3
import base64
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable
from urllib.parse import quote

import requests


DEVICE_CODE_URL = "https://oauth.yandex.com/device/code"
TOKEN_URL = "https://oauth.yandex.com/token"
VERIFY_URL = "https://oauth.yandex.com/device"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
DISK_API = "https://cloud-api.yandex.net/v1/disk/resources"
POLL_MARGIN_SECONDS = 60

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")).resolve()
CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config/python-uploader")).resolve()
TOKEN_FILE = CONFIG_DIR / "token.json"
REMOTE_ROOT = os.environ.get("YDSK_REMOTE_PATH", "").strip().strip("/")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_ID = os.environ.get("TELEGRAM_ADMIN", "").strip()
YANDEX_CLIENT_ID = os.environ.get("YANDEX_CLIENT_ID", "").strip()
YANDEX_CLIENT_SECRET = os.environ.get("YANDEX_CLIENT_SECRET", "").strip()
YANDEX_OAUTH_SCOPE = os.environ.get("YANDEX_OAUTH_SCOPE", "").strip()
YANDEX_OAUTH_DEBUG = os.environ.get("YANDEX_OAUTH_DEBUG", "0").strip() == "1"
TELEGRAM_AUTH_TIMEOUT = int(os.environ.get("TELEGRAM_AUTH_TIMEOUT", "900"))
SCAN_INTERVAL_SECONDS = int(os.environ.get("SCAN_INTERVAL_SECONDS", "15"))
LOG_LEVEL = os.environ.get("UPLOADER_LOG_LEVEL", "INFO").strip().upper()


logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="[%(asctime)s] %(levelname)s %(message)s",
)
LOG = logging.getLogger("ydsk-python-uploader")


class AppError(RuntimeError):
    pass


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str
    token_type: str
    expiry: datetime

    @classmethod
    def from_dict(cls, payload):
        expiry_text = payload.get("expiry", "")
        if not expiry_text:
            expiry = datetime.now(timezone.utc)
        else:
            expiry = datetime.fromisoformat(expiry_text.replace("Z", "+00:00"))
        return cls(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token", ""),
            token_type=payload.get("token_type", "bearer"),
            expiry=expiry.astimezone(timezone.utc),
        )

    def to_dict(self):
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expiry": self.expiry.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }


@dataclass
class FileMeta:
    size: int
    md5: str


def debug(message):
    if YANDEX_OAUTH_DEBUG:
        LOG.info("[yandex-oauth-debug] %s", message)


def require_env():
    missing = []
    for name, value in (
        ("YDSK_REMOTE_PATH", REMOTE_ROOT),
        ("YANDEX_CLIENT_ID", YANDEX_CLIENT_ID),
        ("YANDEX_CLIENT_SECRET", YANDEX_CLIENT_SECRET),
        ("TELEGRAM_BOT_TOKEN", BOT_TOKEN),
        ("TELEGRAM_ADMIN", ADMIN_ID),
    ):
        if not value:
            missing.append(name)
    if missing:
        raise AppError(f"missing required environment variables: {', '.join(missing)}")


def telegram_api(method, params=None):
    payload = params or {}
    response = requests.post(
        TELEGRAM_API.format(token=BOT_TOKEN, method=method),
        data=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise AppError(f"telegram api error for {method}: {data}")
    return data["result"]


def telegram_send(text):
    telegram_api("sendMessage", {"chat_id": ADMIN_ID, "text": text})


def telegram_initial_offset():
    updates = telegram_api("getUpdates", {"timeout": 0, "limit": 100})
    if not updates:
        return 0
    return updates[-1]["update_id"] + 1


def oauth_basic_auth_header():
    auth = base64.b64encode(f"{YANDEX_CLIENT_ID}:{YANDEX_CLIENT_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {auth}"}


def oauth_request(url, data, headers=None):
    debug(f"POST {url} data={json.dumps(data, ensure_ascii=False)}")
    response = requests.post(url, data=data, headers=headers or {}, timeout=60)
    debug(f"POST {url} status={response.status_code} body={response.text}")
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise AppError(f"oauth endpoint returned non-json response: {response.text}") from exc
    if response.ok:
        return payload
    error = payload.get("error") or ""
    description = payload.get("error_description") or ""
    message = description or error or response.text
    exc = AppError(message)
    setattr(exc, "oauth_error", error)
    setattr(exc, "oauth_error_description", description)
    raise exc


def save_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def load_token():
    if not TOKEN_FILE.exists():
        return None
    return OAuthToken.from_dict(json.loads(TOKEN_FILE.read_text()))


def save_token(token: OAuthToken):
    save_json(TOKEN_FILE, token.to_dict())


def apply_token(target: OAuthToken, source: OAuthToken):
    target.access_token = source.access_token
    target.refresh_token = source.refresh_token
    target.token_type = source.token_type
    target.expiry = source.expiry


def wait_for_auth_command():
    deadline = time.time() + TELEGRAM_AUTH_TIMEOUT
    offset = telegram_initial_offset()
    telegram_send(
        "Yandex Disk token is missing.\n"
        "Send /auth to start authorization or /cancel to abort."
    )
    while time.time() < deadline:
        updates = telegram_api("getUpdates", {"timeout": 30, "offset": offset})
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message") or {}
            chat = message.get("chat") or {}
            if str(chat.get("id")) != ADMIN_ID:
                continue
            text = (message.get("text") or "").strip().lower()
            if text == "/cancel":
                raise AppError("authorization cancelled from telegram")
            if text == "/auth":
                return
    raise AppError("timeout waiting for /auth in telegram")


def request_device_code():
    payload = {
        "client_id": YANDEX_CLIENT_ID,
        "device_id": str(uuid.uuid4()),
        "device_name": "ydsk-python-uploader",
    }
    if YANDEX_OAUTH_SCOPE:
        payload["scope"] = YANDEX_OAUTH_SCOPE
    return oauth_request(DEVICE_CODE_URL, payload)


def poll_token(device_code, interval):
    deadline = time.time() + TELEGRAM_AUTH_TIMEOUT
    attempt = 0
    headers = oauth_basic_auth_header()
    while time.time() < deadline:
        attempt += 1
        try:
            debug(f"poll_token attempt={attempt} device_code={device_code}")
            return oauth_request(
                TOKEN_URL,
                {"grant_type": "device_code", "code": device_code},
                headers=headers,
            )
        except AppError as exc:
            error = getattr(exc, "oauth_error", "")
            description = getattr(exc, "oauth_error_description", "")
            debug(
                "poll_token error "
                f"attempt={attempt} error={error or str(exc)} "
                f"description={description or str(exc)}"
            )
            if error in {"authorization_pending", "slow_down"}:
                time.sleep(interval + (5 if error == "slow_down" else 0))
                continue
            if str(exc) == "User has not yet authorized your application":
                time.sleep(interval)
                continue
            raise
    raise AppError("timeout waiting for Yandex OAuth confirmation")


def refresh_token(token: OAuthToken):
    if not token.refresh_token:
        raise AppError("saved token has no refresh_token")
    payload = oauth_request(
        TOKEN_URL,
        {"grant_type": "refresh_token", "refresh_token": token.refresh_token},
        headers=oauth_basic_auth_header(),
    )
    refreshed = OAuthToken(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", token.refresh_token),
        token_type=payload.get("token_type", token.token_type),
        expiry=datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in", 0))),
    )
    save_token(refreshed)
    LOG.info("oauth token refreshed")
    return refreshed


def ensure_token():
    token = load_token()
    if token is None:
        wait_for_auth_command()
        device = request_device_code()
        debug(
            "device_code issued "
            f"device_code={device.get('device_code')} "
            f"user_code={device.get('user_code')} "
            f"interval={device.get('interval')} "
            f"expires_in={device.get('expires_in')}"
        )
        telegram_send(
            "Open the link below in a browser logged into the required Yandex account and enter the confirmation code.\n\n"
            f"{VERIFY_URL}\n\n"
            f"Code: {device['user_code']}\n"
            f"Expires in: {device.get('expires_in', 0)} seconds"
        )
        response = poll_token(device["device_code"], int(device.get("interval", 5)))
        token = OAuthToken(
            access_token=response["access_token"],
            refresh_token=response.get("refresh_token", ""),
            token_type=response.get("token_type", "bearer"),
            expiry=datetime.now(timezone.utc) + timedelta(seconds=int(response.get("expires_in", 0))),
        )
        save_token(token)
        telegram_send("Yandex Disk authorization completed successfully.")
        LOG.info("oauth authorization completed")
        return token
    if token.expiry <= datetime.now(timezone.utc) + timedelta(seconds=POLL_MARGIN_SECONDS):
        return refresh_token(token)
    return token


def disk_headers(token: OAuthToken):
    return {"Authorization": f"OAuth {token.access_token}"}


def disk_request(method, url, token: OAuthToken, expected=(200, 201, 202, 204), **kwargs):
    request_headers = dict(kwargs.pop("headers", {}))
    request_headers.update(disk_headers(token))
    response = requests.request(method, url, headers=request_headers, timeout=300, **kwargs)
    if response.status_code in {401, 403}:
        fresh = refresh_token(token)
        retry_headers = dict(kwargs.pop("retry_headers", {}))
        retry_headers.update(disk_headers(fresh))
        response = requests.request(method, url, headers=retry_headers, timeout=300, **kwargs)
        apply_token(token, fresh)
    if response.status_code not in expected:
        raise AppError(f"disk api {method} {url} failed: {response.status_code} {response.text}")
    return response


def wait_operation(href, token: OAuthToken):
    while True:
        response = disk_request("GET", href, token, expected=(200,))
        payload = response.json()
        status = payload.get("status")
        if status == "success":
            return
        if status == "failed":
            raise AppError(f"operation failed: {payload}")
        time.sleep(2)


def disk_json(method, url, token: OAuthToken, expected=(200, 201, 202, 204), **kwargs):
    response = disk_request(method, url, token, expected=expected, **kwargs)
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def ensure_remote_dirs(rel_path: str, token: OAuthToken):
    parts = Path(rel_path).parts[:-1]
    current = ""
    all_parts = [part for part in Path(REMOTE_ROOT).parts if part] + list(parts)
    if not all_parts:
        return
    for part in all_parts:
        current = f"{current}/{part}" if current else part
        url = f"{DISK_API}?path={quote(current, safe='/')}"
        response = requests.put(url, headers=disk_headers(token), timeout=120)
        if response.status_code in {201, 409}:
            continue
        if response.status_code in {401, 403}:
            fresh = refresh_token(token)
            apply_token(token, fresh)
            response = requests.put(url, headers=disk_headers(token), timeout=120)
            if response.status_code in {201, 409}:
                continue
        raise AppError(f"mkdir failed for {current}: {response.status_code} {response.text}")


def md5sum(path: Path):
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_signature(path: Path):
    return FileMeta(size=path.stat().st_size, md5=md5sum(path))


def normalize_remote_rel(full_path: str):
    root_prefix = f"{REMOTE_ROOT}/" if REMOTE_ROOT else ""
    if root_prefix and full_path.startswith(root_prefix):
        return full_path[len(root_prefix) :]
    if full_path == REMOTE_ROOT:
        return ""
    return full_path


def list_remote_files(token: OAuthToken) -> Dict[str, FileMeta]:
    result = {}
    queue = [REMOTE_ROOT]

    while queue:
        current = queue.pop(0)
        offset = 0

        while True:
            url = (
                f"{DISK_API}?path={quote(current, safe='/')}"
                "&limit=1000"
                f"&offset={offset}"
                "&fields=_embedded.items.path,_embedded.items.type,_embedded.items.md5,_embedded.items.size"
            )
            payload = disk_json("GET", url, token, expected=(200, 404))
            if payload is None:
                break
            if isinstance(payload, dict) and payload.get("error") == "DiskNotFoundError":
                return result

            embedded = (payload or {}).get("_embedded") or {}
            items = embedded.get("items") or []
            for item in items:
                item_path = item["path"].replace("disk:/", "", 1)
                item_type = item.get("type")
                if item_type == "dir":
                    queue.append(item_path)
                    continue
                rel_path = normalize_remote_rel(item_path)
                if not rel_path:
                    continue
                result[rel_path] = FileMeta(size=int(item.get("size", 0)), md5=item.get("md5", ""))

            total = embedded.get("total", 0)
            limit = embedded.get("limit", 1000)
            offset += len(items)
            if offset >= total or not items or len(items) < limit:
                break

    return result


def delete_remote_file(rel_path: str, token: OAuthToken):
    final_remote = f"{REMOTE_ROOT}/{rel_path}".strip("/")
    url = f"{DISK_API}?path={quote(final_remote, safe='/')}&permanently=true"
    response = disk_request("DELETE", url, token, expected=(202, 204, 404))
    if response.status_code == 202:
        wait_operation(response.json()["href"], token)
    LOG.info("remote delete path=%s", final_remote)


def remote_paths(final_rel_path: str):
    final_remote = f"{REMOTE_ROOT}/{final_rel_path}".strip("/")
    temp_remote = f"{final_remote}.file"
    return temp_remote, final_remote


def local_temp_path(path: Path):
    return path.with_name(f"{path.name}.file")


def upload_file(local_path: Path, token: OAuthToken):
    rel_path = local_path.relative_to(DATA_DIR).as_posix()
    if local_path.name.endswith(".file"):
        current_local = local_path
        restore_path = local_path.with_name(local_path.name[: -len(".file")])
        final_rel_path = rel_path[: -len(".file")]
    else:
        current_local = local_temp_path(local_path)
        local_path.rename(current_local)
        restore_path = local_path
        final_rel_path = rel_path

    temp_remote, final_remote = remote_paths(final_rel_path)

    LOG.info("upload start local=%s remote=%s", current_local, final_remote)
    try:
        ensure_remote_dirs(final_remote, token)

        upload_url = (
            f"{DISK_API}/upload?"
            f"path={quote(temp_remote, safe='/')}&overwrite=true"
        )
        response = disk_request("GET", upload_url, token, expected=(200,))
        href = response.json()["href"]

        with current_local.open("rb") as handle:
            put_response = requests.put(href, data=handle, timeout=3600)
        if put_response.status_code not in {201, 202}:
            raise AppError(f"upload failed for {current_local}: {put_response.status_code} {put_response.text}")

        move_url = (
            f"{DISK_API}/move?"
            f"from={quote(temp_remote, safe='/')}&path={quote(final_remote, safe='/')}&overwrite=true"
        )
        move_response = disk_request("POST", move_url, token, expected=(201, 202))
        if move_response.status_code == 202:
            wait_operation(move_response.json()["href"], token)

        if current_local != restore_path:
            current_local.rename(restore_path)
        LOG.info("upload success remote=%s local_kept=%s", final_remote, restore_path)
    except Exception:
        if current_local.exists() and current_local != restore_path and not restore_path.exists():
            current_local.rename(restore_path)
        raise


def list_files() -> Iterable[Path]:
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    files = [path for path in DATA_DIR.rglob("*") if path.is_file()]
    files.sort()
    return files


def collect_local_files():
    result = {}
    for path in list_files():
        rel = path.relative_to(DATA_DIR).as_posix()
        if rel.endswith(".file"):
            continue
        result[rel] = local_signature(path)
    return result


def process_once(token: OAuthToken):
    local_files = collect_local_files()
    remote_files = list_remote_files(token)

    for rel_path, meta in local_files.items():
        remote_meta = remote_files.get(rel_path)
        if remote_meta is not None and remote_meta.size == meta.size and remote_meta.md5 == meta.md5:
            continue
        upload_file(DATA_DIR / rel_path, token)

    stale_remote_paths = []
    for rel_path in remote_files:
        if rel_path.endswith(".file"):
            stale_remote_paths.append(rel_path)
            continue
        if rel_path not in local_files:
            stale_remote_paths.append(rel_path)

    for rel_path in sorted(stale_remote_paths):
        delete_remote_file(rel_path, token)


def main():
    require_env()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG.info("starting python uploader data_dir=%s remote_root=%s", DATA_DIR, REMOTE_ROOT)
    token = ensure_token()
    while True:
        try:
            token = ensure_token()
            process_once(token)
        except Exception as exc:
            LOG.exception("processing failed: %s", exc)
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as exc:
        print(f"network error: {exc}", file=sys.stderr)
        sys.exit(1)
    except AppError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
