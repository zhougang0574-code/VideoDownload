import base64
import math
import os
import subprocess
import tempfile

import imageio_ffmpeg
import requests

from .auth import USER_AGENT

COMMON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://www.bilibili.com",
}

PREUPLOAD_URL = "https://member.bilibili.com/preupload"
COVER_UP_URL = "https://member.bilibili.com/x/vu/web/cover/up"
ADD_URL = "https://member.bilibili.com/x/vu/web/add"
GEETEST_PRE_URL = "https://member.bilibili.com/x/geetest/pre/add"


def _preupload(filepath, cookies):
    name = os.path.basename(filepath)
    size = os.path.getsize(filepath)
    resp = requests.get(
        PREUPLOAD_URL,
        params={
            "upcdn": "bda2",
            "probe_version": "20221109",
            "r": "upos",
            "profile": "ugcupos/bup",
            "ssl": 0,
            "version": "2.8.12",
            "build": 2081200,
            "name": name,
            "size": size,
        },
        headers=COMMON_HEADERS,
        cookies=cookies,
        timeout=15,
    )
    ret = resp.json()
    if "auth" not in ret:
        raise RuntimeError(f"预上传失败：{ret}")
    return ret


def upload_video_file(filepath, cookies, on_progress=None):
    """Upload the local file to B站's upos storage and return the
    bili-assigned file identifier to reference in submit_video()."""
    ret = _preupload(filepath, cookies)
    endpoint = ret["endpoint"]
    upos_uri = ret["upos_uri"]
    url = f"https:{endpoint}/{upos_uri.replace('upos://', '')}"
    auth_headers = {**COMMON_HEADERS, "X-Upos-Auth": ret["auth"]}
    chunk_size = ret["chunk_size"]
    biz_id = ret["biz_id"]
    total_size = os.path.getsize(filepath)
    chunks = math.ceil(total_size / chunk_size)

    init_resp = requests.post(f"{url}?uploads&output=json", headers=auth_headers, timeout=15)
    upload_id = init_resp.json()["upload_id"]

    parts = []
    uploaded = 0
    with open(filepath, "rb") as f:
        for chunk_index in range(chunks):
            data = f.read(chunk_size)
            start = chunk_index * chunk_size
            params = {
                "uploadId": upload_id,
                "chunks": chunks,
                "total": total_size,
                "chunk": chunk_index,
                "size": len(data),
                "partNumber": chunk_index + 1,
                "start": start,
                "end": start + len(data),
            }
            resp = requests.put(url, params=params, data=data, headers=auth_headers, timeout=60)
            resp.raise_for_status()
            parts.append({"partNumber": chunk_index + 1, "eTag": "etag"})
            uploaded += len(data)
            if on_progress:
                on_progress(uploaded / total_size * 100)

    complete_resp = requests.post(
        url,
        params={
            "name": os.path.basename(filepath),
            "uploadId": upload_id,
            "biz_id": biz_id,
            "output": "json",
            "profile": "ugcupos/bup",
        },
        json={"parts": parts},
        headers=auth_headers,
        timeout=30,
    ).json()
    if complete_resp.get("OK") != 1:
        raise RuntimeError(f"分片合并失败：{complete_resp}")

    return os.path.splitext(os.path.basename(upos_uri))[0]


def extract_cover_frame(video_path):
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    fd, cover_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    subprocess.run(
        [ffmpeg_exe, "-y", "-i", video_path, "-ss", "00:00:01", "-frames:v", "1", cover_path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return cover_path


def upload_cover(cover_path, cookies):
    bili_jct = cookies["bili_jct"]
    with open(cover_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    resp = requests.post(
        COVER_UP_URL,
        data={"cover": f"data:image/jpeg;base64,{image_b64}", "csrf": bili_jct},
        headers=COMMON_HEADERS,
        cookies=cookies,
        timeout=30,
    )
    data = resp.json()
    if data.get("data") is None:
        raise RuntimeError(f"封面上传失败：{data}")
    return data["data"]["url"]


def submit_video(title, desc, tid, tag, cover_url, bili_filename, part_title, cookies):
    bili_jct = cookies["bili_jct"]
    requests.get(GEETEST_PRE_URL, headers=COMMON_HEADERS, cookies=cookies, timeout=10)

    payload = {
        "copyright": 1,
        "source": "",
        "tid": tid,
        "cover": cover_url.replace("http:", ""),
        "title": title,
        "desc_format_id": 0,
        "desc": desc,
        "dynamic": "",
        "subtitle": {"open": 0, "lan": ""},
        "tag": tag,
        "videos": [{"title": part_title, "filename": bili_filename, "desc": ""}],
        "dtime": None,
    }
    resp = requests.post(
        ADD_URL,
        params={"csrf": bili_jct},
        json=payload,
        headers=COMMON_HEADERS,
        cookies=cookies,
        timeout=15,
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"投稿提交失败：{data.get('message')}（code={data.get('code')}）")
    return data["data"]
