import os
import re
import subprocess
import tempfile

import imageio_ffmpeg
import requests

from .auth import USER_AGENT

COMMON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://www.bilibili.com",
}

DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads")

ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename(name):
    return ILLEGAL_CHARS_RE.sub("_", name).strip()


def download_stream(url, dest_path, cookies, on_progress=None):
    resp = requests.get(url, headers=COMMON_HEADERS, cookies=cookies, stream=True, timeout=30)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)
            if total and on_progress:
                on_progress(downloaded / total * 100)


def merge_av(video_path, audio_path, out_path):
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c", "copy",
            out_path,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def download_video(title, video_url, audio_url, cookies, on_stage=None):
    """on_stage(stage, percent) is called as the download moves through
    'downloading_video' -> 'downloading_audio' -> 'merging' -> 'done'."""
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    out_path = os.path.join(DOWNLOADS_DIR, f"{sanitize_filename(title)}.mp4")

    def emit(stage, percent):
        if on_stage:
            on_stage(stage, percent)

    with tempfile.TemporaryDirectory() as tmp_dir:
        video_tmp = os.path.join(tmp_dir, "video.m4s")
        audio_tmp = os.path.join(tmp_dir, "audio.m4s")

        download_stream(video_url, video_tmp, cookies, lambda p: emit("downloading_video", p))
        download_stream(audio_url, audio_tmp, cookies, lambda p: emit("downloading_audio", p))

        emit("merging", 100)
        merge_av(video_tmp, audio_tmp, out_path)

    emit("done", 100)
    return out_path
