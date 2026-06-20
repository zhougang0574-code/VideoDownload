import json
import os
import threading
import time

import qrcode
import requests
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_CONFIG_PATH = os.path.join(PROJECT_ROOT, "youtube_client.json")
TOKEN_PATH = os.path.join(PROJECT_ROOT, "youtube_token.json")
STATIC_DIR = os.path.join(PROJECT_ROOT, "static")
QRCODE_STATIC_PATH = os.path.join(STATIC_DIR, "youtube_qrcode.png")

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

_login_state = {"status": "idle", "message": ""}
_login_lock = threading.Lock()


def load_client_config():
    if not os.path.exists(CLIENT_CONFIG_PATH):
        return None
    with open(CLIENT_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_client_config(client_id, client_secret):
    with open(CLIENT_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"client_id": client_id, "client_secret": client_secret}, f, ensure_ascii=False, indent=2)


def load_token():
    if not os.path.exists(TOKEN_PATH):
        return None
    with open(TOKEN_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_token(refresh_token):
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump({"refresh_token": refresh_token}, f, ensure_ascii=False, indent=2)


def get_login_state():
    with _login_lock:
        return dict(_login_state)


def _set_login_state(**kwargs):
    with _login_lock:
        _login_state.clear()
        _login_state.update(kwargs)


def get_current_credentials():
    client = load_client_config()
    token = load_token()
    if not client or not token:
        return None
    creds = Credentials(
        token=None,
        refresh_token=token["refresh_token"],
        token_uri=TOKEN_URL,
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        scopes=SCOPES,
    )
    try:
        creds.refresh(GoogleAuthRequest())
    except Exception:
        return None
    return creds


def _login_worker(client_id, client_secret):
    try:
        resp = requests.post(
            DEVICE_CODE_URL,
            data={"client_id": client_id, "scope": " ".join(SCOPES)},
            timeout=15,
        )
        data = resp.json()
        if "device_code" not in data:
            _set_login_state(status="error", message=f"获取设备码失败：{data}")
            return

        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_url = data.get("verification_url") or data.get("verification_uri")
        interval = data.get("interval", 5)
        expires_in = data.get("expires_in", 1800)

        os.makedirs(STATIC_DIR, exist_ok=True)
        qr = qrcode.QRCode(border=1)
        qr.add_data(verification_url)
        qr.make()
        qr.make_image().save(QRCODE_STATIC_PATH)

        _set_login_state(
            status="waiting",
            message="请打开页面并输入验证码确认登录",
            verification_url=verification_url,
            user_code=user_code,
        )

        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            resp = requests.post(
                TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                timeout=15,
            )
            result = resp.json()

            if "access_token" in result:
                save_token(result["refresh_token"])
                _set_login_state(status="success", message="登录成功")
                return

            error = result.get("error")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 5
                continue
            elif error == "access_denied":
                _set_login_state(status="denied", message="登录被拒绝")
                return
            elif error == "expired_token":
                _set_login_state(status="expired", message="验证码已过期，请重新获取")
                return
            else:
                _set_login_state(status="error", message=f"登录失败：{result}")
                return

        _set_login_state(status="expired", message="验证码已过期，请重新获取")
    except Exception as e:
        _set_login_state(status="error", message=str(e))


def start_login():
    client = load_client_config()
    if not client:
        _set_login_state(status="error", message="还没有配置 Google Client ID/Secret")
        return

    with _login_lock:
        if _login_state.get("status") in ("starting", "waiting"):
            return
        _login_state.clear()
        _login_state.update(status="starting", message="正在获取设备码...")

    threading.Thread(
        target=_login_worker,
        args=(client["client_id"], client["client_secret"]),
        daemon=True,
    ).start()
