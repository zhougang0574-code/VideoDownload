import json
import os
import threading
import time

import qrcode
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIES_PATH = os.path.join(PROJECT_ROOT, "cookies.json")
STATIC_DIR = os.path.join(PROJECT_ROOT, "static")
QRCODE_STATIC_PATH = os.path.join(STATIC_DIR, "qrcode.png")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

QRCODE_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QRCODE_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

# Module-level state shared between the background login thread and the Flask routes.
_login_state = {"status": "idle", "message": ""}
_login_lock = threading.Lock()
_current_cookies = None


def load_cookies():
    if not os.path.exists(COOKIES_PATH):
        return None
    with open(COOKIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cookies(cookies):
    with open(COOKIES_PATH, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)


def is_login_valid(cookies):
    if not cookies:
        return False
    try:
        resp = requests.get(
            NAV_URL,
            cookies=cookies,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        data = resp.json()
        return bool(data.get("data", {}).get("isLogin"))
    except requests.RequestException:
        return False


def get_current_cookies():
    global _current_cookies
    if _current_cookies is None:
        cookies = load_cookies()
        if is_login_valid(cookies):
            _current_cookies = cookies
    return _current_cookies


def get_login_state():
    with _login_lock:
        return dict(_login_state)


def _set_login_state(**kwargs):
    with _login_lock:
        _login_state.clear()
        _login_state.update(kwargs)


def _login_worker(poll_interval, timeout):
    global _current_cookies
    try:
        resp = requests.get(QRCODE_GENERATE_URL, headers={"User-Agent": USER_AGENT}, timeout=10)
        data = resp.json()["data"]
        qrcode_url = data["url"]
        qrcode_key = data["qrcode_key"]

        os.makedirs(STATIC_DIR, exist_ok=True)
        qr = qrcode.QRCode(border=1)
        qr.add_data(qrcode_url)
        qr.make()
        qr.make_image().save(QRCODE_STATIC_PATH)

        _set_login_state(status="waiting", message="请使用哔哩哔哩APP扫码登录")

        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = requests.get(
                QRCODE_POLL_URL,
                params={"qrcode_key": qrcode_key},
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
            result = resp.json()["data"]
            code = result["code"]

            if code == 0:
                cookies = dict(requests.utils.dict_from_cookiejar(resp.cookies))
                save_cookies(cookies)
                _current_cookies = cookies
                _set_login_state(status="success", message="登录成功")
                return
            elif code == 86038:
                _set_login_state(status="expired", message="二维码已失效，请重新获取")
                return
            elif code == 86090:
                _set_login_state(status="scanned", message="已扫码，请在手机上确认登录")
            elif code == 86101:
                _set_login_state(status="waiting", message="请使用哔哩哔哩APP扫码登录")

            time.sleep(poll_interval)

        _set_login_state(status="timeout", message="登录超时，请重新获取二维码")
    except Exception as e:
        _set_login_state(status="error", message=str(e))


def start_login(poll_interval=2, timeout=180):
    with _login_lock:
        if _login_state.get("status") in ("starting", "waiting", "scanned"):
            return
        _login_state.clear()
        _login_state.update(status="starting", message="正在生成二维码...")

    threading.Thread(target=_login_worker, args=(poll_interval, timeout), daemon=True).start()
