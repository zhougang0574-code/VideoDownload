import re

import requests

from .auth import USER_AGENT

BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")

VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_URL = "https://api.bilibili.com/x/player/playurl"

COMMON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://www.bilibili.com",
}


def extract_bvid(text):
    text = text.strip()
    match = BVID_RE.search(text)
    if match:
        return match.group(0)

    if text.startswith("http://") or text.startswith("https://"):
        try:
            resp = requests.get(text, headers=COMMON_HEADERS, timeout=10, allow_redirects=True)
            match = BVID_RE.search(resp.url)
            if match:
                return match.group(0)
        except requests.RequestException:
            pass

    return None


def get_view_info(bvid, cookies):
    resp = requests.get(
        VIEW_URL,
        params={"bvid": bvid},
        headers=COMMON_HEADERS,
        cookies=cookies,
        timeout=10,
    )
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"获取视频信息失败：{payload.get('message')}")

    data = payload["data"]
    return {
        "title": data["title"],
        "cid": data["cid"],
        "pages": data.get("pages", []),
    }


def get_play_url(bvid, cid, cookies):
    resp = requests.get(
        PLAYURL_URL,
        params={
            "bvid": bvid,
            "cid": cid,
            "qn": 127,
            "fnval": 4048,
            "fourk": 1,
        },
        headers=COMMON_HEADERS,
        cookies=cookies,
        timeout=10,
    )
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"获取播放地址失败：{payload.get('message')}")

    data = payload["data"]
    dash = data.get("dash")
    if not dash:
        raise RuntimeError("该视频未返回 DASH 流，暂不支持下载（可能是直播/番剧等特殊类型）。")

    best_video = max(dash["video"], key=lambda v: v["id"])
    best_audio = max(dash["audio"], key=lambda a: a["id"])
    return best_video["baseUrl"], best_audio["baseUrl"]
