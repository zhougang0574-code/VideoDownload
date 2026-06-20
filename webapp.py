import os
import threading
import uuid

from flask import Flask, jsonify, render_template, request, send_from_directory

from bili.api import extract_bvid, get_play_url, get_view_info
from bili.auth import get_current_cookies, get_login_state, start_login
from bili.downloader import DOWNLOADS_DIR, download_video, sanitize_filename
from bili.uploader import extract_cover_frame, submit_video, upload_cover, upload_video_file
from youtube.auth import (
    get_current_credentials,
    get_login_state as get_youtube_login_state,
    load_client_config,
    save_client_config,
    start_login as start_youtube_login,
)
from youtube.downloader import DOWNLOADS_DIR as YOUTUBE_DOWNLOADS_DIR, download_video as download_youtube_video
from youtube.uploader import upload_video_file as upload_youtube_video
from subtitle.burner import burn_subtitles, write_srt
from subtitle.transcriber import extract_audio, transcribe
from subtitle.translator import load_deepl_key, save_deepl_key, translate_segments

app = Flask(__name__)

UPLOAD_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads_tmp")
YOUTUBE_UPLOAD_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "youtube_uploads_tmp")
SUBTITLE_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subtitle_tmp")
SUBTITLE_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subtitled_videos")

TASKS = {}
_tasks_lock = threading.Lock()


def _update_task(task_id, **fields):
    with _tasks_lock:
        TASKS[task_id].update(fields)


def _run_download(task_id, text):
    print(f"[下载] 开始：{text}")
    cookies = get_current_cookies()
    try:
        _update_task(task_id, stage="parsing", percent=0)
        bvid = extract_bvid(text)
        if not bvid:
            print(f"[下载] 失败：无法识别 BV 号（{text}）")
            _update_task(task_id, stage="error", error="没有从输入里识别出 BV 号，请检查链接是否正确。")
            return

        _update_task(task_id, stage="fetching_info", percent=0)
        info = get_view_info(bvid, cookies)
        _update_task(task_id, title=info["title"])

        video_url, audio_url = get_play_url(bvid, info["cid"], cookies)

        def on_stage(stage, percent):
            _update_task(task_id, stage=stage, percent=percent)

        out_path = download_video(info["title"], video_url, audio_url, cookies, on_stage)
        _update_task(task_id, stage="done", percent=100, output_path=out_path)
        print(f"[下载] 完成：{out_path}")
    except Exception as e:
        print(f"[下载] 失败：{e}")
        _update_task(task_id, stage="error", error=str(e))


def _run_upload(task_id, video_path, cover_path, title, desc, tid, tag):
    print(f"[投稿] 开始：{title}")
    cookies = get_current_cookies()
    try:
        _update_task(task_id, stage="uploading_video", percent=0)

        def on_progress(percent):
            _update_task(task_id, stage="uploading_video", percent=percent)

        bili_filename = upload_video_file(video_path, cookies, on_progress)

        generated_cover = None
        if not cover_path:
            _update_task(task_id, stage="extracting_cover", percent=100)
            generated_cover = extract_cover_frame(video_path)
            cover_path = generated_cover

        _update_task(task_id, stage="uploading_cover", percent=100)
        cover_url = upload_cover(cover_path, cookies)
        if generated_cover:
            os.remove(generated_cover)

        _update_task(task_id, stage="submitting", percent=100)
        data = submit_video(title, desc, tid, tag, cover_url, bili_filename, title, cookies)

        _update_task(task_id, stage="done", percent=100, bvid=data.get("bvid"))
        print(f"[投稿] 完成：{data}")
    except Exception as e:
        print(f"[投稿] 失败：{e}")
        _update_task(task_id, stage="error", error=str(e))
    finally:
        if os.path.exists(video_path):
            os.remove(video_path)
        if cover_path and os.path.exists(cover_path):
            os.remove(cover_path)


def _run_youtube_download(task_id, url):
    print(f"[YouTube下载] 开始：{url}")
    try:
        _update_task(task_id, stage="downloading", percent=0)

        def on_progress(percent):
            _update_task(task_id, stage="downloading", percent=percent)

        title, out_path = download_youtube_video(url, on_progress)
        _update_task(task_id, stage="done", percent=100, title=title, output_path=out_path)
        print(f"[YouTube下载] 完成：{out_path}")
    except Exception as e:
        print(f"[YouTube下载] 失败：{e}")
        _update_task(task_id, stage="error", error=str(e))


def _run_youtube_upload(task_id, video_path, title, desc, tags, privacy):
    print(f"[YouTube上传] 开始：{title}")
    try:
        _update_task(task_id, stage="uploading", percent=0)
        credentials = get_current_credentials()
        if credentials is None:
            _update_task(task_id, stage="error", error="未登录Google账号或登录已失效")
            return

        def on_progress(percent):
            _update_task(task_id, stage="uploading", percent=percent)

        video_id = upload_youtube_video(video_path, title, desc, tags, privacy, credentials, on_progress)
        _update_task(task_id, stage="done", percent=100, video_id=video_id)
        print(f"[YouTube上传] 完成：{video_id}")
    except Exception as e:
        print(f"[YouTube上传] 失败：{e}")
        _update_task(task_id, stage="error", error=str(e))
    finally:
        if os.path.exists(video_path):
            os.remove(video_path)


def _run_subtitle(task_id, video_path, title):
    print(f"[字幕] 开始：{title}")
    audio_path = os.path.join(SUBTITLE_TMP_DIR, f"{task_id}.wav")
    srt_path = os.path.join(SUBTITLE_TMP_DIR, f"{task_id}.srt")
    try:
        _update_task(task_id, stage="extracting_audio", percent=0)
        extract_audio(video_path, audio_path)

        _update_task(task_id, stage="transcribing", percent=0)

        def on_transcribe_progress(percent):
            _update_task(task_id, stage="transcribing", percent=percent)

        segments, language, duration = transcribe(audio_path, on_transcribe_progress)

        if language != "zh":
            _update_task(task_id, stage="translating", percent=0)

            def on_translate_progress(percent):
                _update_task(task_id, stage="translating", percent=percent)

            segments = translate_segments(segments, on_translate_progress)

        write_srt(segments, srt_path)

        os.makedirs(SUBTITLE_OUTPUT_DIR, exist_ok=True)
        output_path = os.path.join(SUBTITLE_OUTPUT_DIR, f"{sanitize_filename(title)}.mp4")

        _update_task(task_id, stage="burning_subtitles", percent=0)

        def on_burn_progress(percent):
            _update_task(task_id, stage="burning_subtitles", percent=percent)

        burn_subtitles(video_path, srt_path, output_path, duration, on_burn_progress)

        _update_task(task_id, stage="done", percent=100, output_path=output_path)
        print(f"[字幕] 完成：{output_path}")
    except Exception as e:
        print(f"[字幕] 失败：{e}")
        _update_task(task_id, stage="error", error=str(e))
    finally:
        for path in (video_path, audio_path, srt_path):
            if os.path.exists(path):
                os.remove(path)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/bilibili")
def bilibili_page():
    return render_template("bilibili.html")


@app.route("/youtube")
def youtube_page():
    return render_template("youtube.html")


@app.route("/subtitle")
def subtitle_page():
    return render_template("subtitle.html")


@app.route("/api/bilibili/session")
def api_session():
    return jsonify(logged_in=get_current_cookies() is not None)


@app.route("/api/bilibili/login/start", methods=["POST"])
def api_login_start():
    start_login()
    return jsonify(ok=True)


@app.route("/api/bilibili/login/status")
def api_login_status():
    return jsonify(get_login_state())


@app.route("/api/bilibili/download", methods=["POST"])
def api_download():
    text = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not text:
        return jsonify(error="请输入视频链接或 BV 号"), 400

    task_id = uuid.uuid4().hex
    with _tasks_lock:
        TASKS[task_id] = {"stage": "queued", "percent": 0}
    threading.Thread(target=_run_download, args=(task_id, text), daemon=True).start()
    return jsonify(task_id=task_id)


@app.route("/api/bilibili/download/status/<task_id>")
def api_download_status(task_id):
    with _tasks_lock:
        task = TASKS.get(task_id)
    if task is None:
        return jsonify(error="任务不存在"), 404
    return jsonify(task)


@app.route("/downloads/<path:filename>")
def serve_download(filename):
    return send_from_directory(DOWNLOADS_DIR, filename)


@app.route("/api/bilibili/upload", methods=["POST"])
def api_upload():
    video_file = request.files.get("video")
    if not video_file or not video_file.filename:
        return jsonify(error="请选择要上传的视频文件"), 400

    title = request.form.get("title", "").strip()
    tid = request.form.get("tid", "").strip()
    tag = request.form.get("tag", "").strip()
    desc = request.form.get("desc", "").strip()
    if not title or not tid or not tag:
        return jsonify(error="标题、分区tid、标签都是必填的"), 400
    if not tid.isdigit():
        return jsonify(error="分区tid必须是数字"), 400

    os.makedirs(UPLOAD_TMP_DIR, exist_ok=True)
    task_id = uuid.uuid4().hex

    video_path = os.path.join(UPLOAD_TMP_DIR, f"{task_id}_{video_file.filename}")
    video_file.save(video_path)

    cover_path = None
    cover_file = request.files.get("cover")
    if cover_file and cover_file.filename:
        cover_path = os.path.join(UPLOAD_TMP_DIR, f"{task_id}_cover_{cover_file.filename}")
        cover_file.save(cover_path)

    with _tasks_lock:
        TASKS[task_id] = {"stage": "queued", "percent": 0}
    threading.Thread(
        target=_run_upload,
        args=(task_id, video_path, cover_path, title, desc, int(tid), tag),
        daemon=True,
    ).start()
    return jsonify(task_id=task_id)


@app.route("/api/bilibili/upload/status/<task_id>")
def api_upload_status(task_id):
    with _tasks_lock:
        task = TASKS.get(task_id)
    if task is None:
        return jsonify(error="任务不存在"), 404
    return jsonify(task)


@app.route("/api/youtube/config/status")
def api_youtube_config_status():
    return jsonify(configured=load_client_config() is not None)


@app.route("/api/youtube/config", methods=["POST"])
def api_youtube_config():
    data = request.get_json(silent=True) or {}
    client_id = data.get("client_id", "").strip()
    client_secret = data.get("client_secret", "").strip()
    if not client_id or not client_secret:
        return jsonify(error="Client ID 和 Client Secret 都是必填的"), 400
    save_client_config(client_id, client_secret)
    return jsonify(ok=True)


@app.route("/api/youtube/session")
def api_youtube_session():
    return jsonify(logged_in=get_current_credentials() is not None)


@app.route("/api/youtube/login/start", methods=["POST"])
def api_youtube_login_start():
    start_youtube_login()
    return jsonify(ok=True)


@app.route("/api/youtube/login/status")
def api_youtube_login_status():
    return jsonify(get_youtube_login_state())


@app.route("/api/youtube/download", methods=["POST"])
def api_youtube_download():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not url:
        return jsonify(error="请输入视频链接"), 400

    task_id = uuid.uuid4().hex
    with _tasks_lock:
        TASKS[task_id] = {"stage": "queued", "percent": 0}
    threading.Thread(target=_run_youtube_download, args=(task_id, url), daemon=True).start()
    return jsonify(task_id=task_id)


@app.route("/api/youtube/download/status/<task_id>")
def api_youtube_download_status(task_id):
    with _tasks_lock:
        task = TASKS.get(task_id)
    if task is None:
        return jsonify(error="任务不存在"), 404
    return jsonify(task)


@app.route("/youtube_downloads/<path:filename>")
def serve_youtube_download(filename):
    return send_from_directory(YOUTUBE_DOWNLOADS_DIR, filename)


@app.route("/api/youtube/upload", methods=["POST"])
def api_youtube_upload():
    video_file = request.files.get("video")
    if not video_file or not video_file.filename:
        return jsonify(error="请选择要上传的视频文件"), 400

    title = request.form.get("title", "").strip()
    desc = request.form.get("desc", "").strip()
    tags = request.form.get("tags", "").strip()
    privacy = request.form.get("privacy", "private").strip()
    if not title:
        return jsonify(error="标题是必填的"), 400
    if privacy not in ("private", "unlisted", "public"):
        return jsonify(error="隐私状态不合法"), 400

    os.makedirs(YOUTUBE_UPLOAD_TMP_DIR, exist_ok=True)
    task_id = uuid.uuid4().hex
    video_path = os.path.join(YOUTUBE_UPLOAD_TMP_DIR, f"{task_id}_{video_file.filename}")
    video_file.save(video_path)

    with _tasks_lock:
        TASKS[task_id] = {"stage": "queued", "percent": 0}
    threading.Thread(
        target=_run_youtube_upload,
        args=(task_id, video_path, title, desc, tags, privacy),
        daemon=True,
    ).start()
    return jsonify(task_id=task_id)


@app.route("/api/youtube/upload/status/<task_id>")
def api_youtube_upload_status(task_id):
    with _tasks_lock:
        task = TASKS.get(task_id)
    if task is None:
        return jsonify(error="任务不存在"), 404
    return jsonify(task)


@app.route("/api/subtitle/deepl-key")
def api_subtitle_deepl_key_status():
    return jsonify(configured=load_deepl_key() is not None)


@app.route("/api/subtitle/deepl-key", methods=["POST"])
def api_subtitle_deepl_key_save():
    api_key = (request.get_json(silent=True) or {}).get("api_key", "").strip()
    if not api_key:
        return jsonify(error="API Key 不能为空"), 400
    save_deepl_key(api_key)
    return jsonify(ok=True)


@app.route("/api/subtitle/process", methods=["POST"])
def api_subtitle_process():
    video_file = request.files.get("video")
    if not video_file or not video_file.filename:
        return jsonify(error="请选择要处理的视频文件"), 400

    os.makedirs(SUBTITLE_TMP_DIR, exist_ok=True)
    task_id = uuid.uuid4().hex
    title = os.path.splitext(video_file.filename)[0]
    video_path = os.path.join(SUBTITLE_TMP_DIR, f"{task_id}_{video_file.filename}")
    video_file.save(video_path)

    with _tasks_lock:
        TASKS[task_id] = {"stage": "queued", "percent": 0}
    threading.Thread(target=_run_subtitle, args=(task_id, video_path, title), daemon=True).start()
    return jsonify(task_id=task_id)


@app.route("/api/subtitle/status/<task_id>")
def api_subtitle_status(task_id):
    with _tasks_lock:
        task = TASKS.get(task_id)
    if task is None:
        return jsonify(error="任务不存在"), 404
    return jsonify(task)


@app.route("/subtitled_videos/<path:filename>")
def serve_subtitled_video(filename):
    return send_from_directory(SUBTITLE_OUTPUT_DIR, filename)
