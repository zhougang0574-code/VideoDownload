import os
import subprocess
import sys
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
from subtitle.burner import burn_subtitles, write_srt, write_ass, burn_ass, probe_resolution
from subtitle.transcriber import extract_audio, transcribe
from subtitle.translator import load_deepl_key, save_deepl_key, translate_segments, translate_text
from subtitle.clipper import trim
from subtitle.degrader import degrade_segments
from subtitle.tts import synth_segments, mix_into_video
from subtitle.voice_engines import list_engines, save_voice_config, configured_providers, get_engine

app = Flask(__name__)

UPLOAD_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads_tmp")
YOUTUBE_UPLOAD_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "youtube_uploads_tmp")
SUBTITLE_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subtitle_tmp")
SUBTITLE_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subtitled_videos")
DUB_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dub_tmp")
DUB_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dubbed_videos")

TASKS = {}
_tasks_lock = threading.Lock()

# 鬼畜配音任务在「转录完成、等用户校对」处暂停，每个任务一个 Event 用于放行。
# 不能放进 TASKS（那个字典会被 jsonify），单独存。
DUB_EVENTS = {}
_dub_events_lock = threading.Lock()


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


def _rename_download(out_path, new_title):
    """Rename a downloaded file to a translated title, keeping its extension.
    Returns (title, new_path); falls back to the original on an empty title."""
    safe_title = sanitize_filename(new_title)
    if not safe_title:
        return os.path.splitext(os.path.basename(out_path))[0], out_path
    new_path = os.path.join(os.path.dirname(out_path), f"{safe_title}{os.path.splitext(out_path)[1]}")
    if os.path.abspath(new_path) == os.path.abspath(out_path):
        return safe_title, out_path
    os.replace(out_path, new_path)
    return safe_title, new_path


def _run_youtube_download(task_id, url, translate=False, translate_filename=False):
    print(f"[YouTube下载] 开始：{url}（翻译字幕：{translate}，翻译文件名：{translate_filename}）")
    try:
        _update_task(task_id, stage="downloading", percent=0)

        def on_progress(percent):
            _update_task(task_id, stage="downloading", percent=percent)

        title, out_path = download_youtube_video(url, on_progress)

        if translate_filename:
            zh_title = translate_text(title)
            if zh_title and zh_title != title:
                title, out_path = _rename_download(out_path, zh_title)

        if not translate:
            _update_task(task_id, stage="done", percent=100, title=title, output_path=out_path, translated=False)
            print(f"[YouTube下载] 完成：{out_path}")
            return

        _update_task(task_id, title=title)
        output_path = _generate_subtitled_video(task_id, out_path, title)
        _update_task(task_id, stage="done", percent=100, title=title, output_path=output_path, translated=True)
        print(f"[YouTube下载] 完成（含中文字幕）：{output_path}")
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


def _generate_subtitled_video(task_id, video_path, title):
    """Transcribe a video, translate non-Chinese audio to Chinese, and burn the
    subtitles into a new mp4. Returns the path of the subtitled video. Cleans up
    its own temp files but leaves the source video untouched."""
    os.makedirs(SUBTITLE_TMP_DIR, exist_ok=True)
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

            segments = translate_segments(segments, language, on_translate_progress)

        write_srt(segments, srt_path)

        os.makedirs(SUBTITLE_OUTPUT_DIR, exist_ok=True)
        output_path = os.path.join(SUBTITLE_OUTPUT_DIR, f"{sanitize_filename(title)}.mp4")

        _update_task(task_id, stage="burning_subtitles", percent=0)

        def on_burn_progress(percent):
            _update_task(task_id, stage="burning_subtitles", percent=percent)

        burn_subtitles(video_path, srt_path, output_path, duration, on_burn_progress)
        return output_path
    finally:
        for path in (audio_path, srt_path):
            if os.path.exists(path):
                os.remove(path)


def _run_subtitle(task_id, video_path, title):
    print(f"[字幕] 开始：{title}")
    try:
        output_path = _generate_subtitled_video(task_id, video_path, title)
        _update_task(task_id, stage="done", percent=100, output_path=output_path)
        print(f"[字幕] 完成：{output_path}")
    except Exception as e:
        print(f"[字幕] 失败：{e}")
        _update_task(task_id, stage="error", error=str(e))
    finally:
        if os.path.exists(video_path):
            os.remove(video_path)


def _run_dub(task_id, video_path, start, end, rounds, voice, keep_bg, engine="edge"):
    """鬼畜配音流水线：裁片段 → 转录 →（暂停等校对）→ 翻N遍 → TTS对齐 →
    混音 → 烧新字幕到原字幕上方。中途 awaiting_review 阶段会阻塞等放行。
    engine 选配音引擎；克隆型引擎会用片段原声逐句复刻说话人音色。"""
    print(f"[鬼畜配音] 开始：{video_path} [{start}-{end}] x{rounds}遍 引擎={engine}")
    work = os.path.join(DUB_TMP_DIR, task_id)
    os.makedirs(work, exist_ok=True)
    clip_path = os.path.join(work, "clip.mp4")
    audio_path = os.path.join(work, "audio.wav")
    voice_path = os.path.join(work, "voice.wav")
    mixed_path = os.path.join(work, "mixed.mp4")
    ass_path = os.path.join(work, "subs.ass")
    try:
        _update_task(task_id, stage="trimming", percent=0)
        trim(video_path, start, end, clip_path)
        clip_duration = end - start

        _update_task(task_id, stage="extracting_audio", percent=0)
        extract_audio(clip_path, audio_path)

        _update_task(task_id, stage="transcribing", percent=0)

        def on_tr(p):
            _update_task(task_id, stage="transcribing", percent=p)

        segments, language, duration = transcribe(audio_path, on_tr)
        clip_duration = duration or clip_duration

        # 暂停，把转录结果交给前端校对
        _update_task(task_id, stage="awaiting_review", percent=100, segments=segments)
        event = DUB_EVENTS[task_id]
        event.wait()  # /api/dub/confirm 放行

        with _tasks_lock:
            segments = TASKS[task_id].get("segments", segments)

        _update_task(task_id, stage="degrading", percent=0)

        def on_dg(p):
            _update_task(task_id, stage="degrading", percent=p)

        degraded = degrade_segments(segments, rounds=rounds, on_progress=on_dg)

        _update_task(task_id, stage="synthesizing", percent=0)

        def on_tts(p):
            _update_task(task_id, stage="synthesizing", percent=p)

        # 克隆型引擎用裁好的片段原声（clip_path）逐句切参考音复刻说话人
        synth_segments(
            degraded, voice_path, engine=engine, voice=voice,
            clip_audio=clip_path, work_dir=work, on_progress=on_tts,
        )

        _update_task(task_id, stage="mixing", percent=0)
        mix_into_video(clip_path, voice_path, mixed_path, keep_bg=keep_bg)

        _update_task(task_id, stage="burning", percent=0)
        width, height = probe_resolution(mixed_path)
        write_ass(degraded, ass_path, width, height)

        os.makedirs(DUB_OUTPUT_DIR, exist_ok=True)
        output_path = os.path.join(DUB_OUTPUT_DIR, f"鬼畜配音_{task_id}.mp4")

        def on_burn(p):
            _update_task(task_id, stage="burning", percent=p)

        burn_ass(mixed_path, ass_path, output_path, clip_duration, on_burn)

        _update_task(task_id, stage="done", percent=100, output_path=output_path)
        print(f"[鬼畜配音] 完成：{output_path}")
    except Exception as e:
        print(f"[鬼畜配音] 失败：{e}")
        _update_task(task_id, stage="error", error=str(e))
    finally:
        with _dub_events_lock:
            DUB_EVENTS.pop(task_id, None)
        if os.path.exists(video_path):
            os.remove(video_path)
        try:
            import shutil
            shutil.rmtree(work, ignore_errors=True)
        except Exception:
            pass


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


@app.route("/dub")
def dub_page():
    return render_template("dub.html")


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
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    translate = bool(data.get("translate"))
    translate_filename = bool(data.get("translate_filename"))
    if not url:
        return jsonify(error="请输入视频链接"), 400

    task_id = uuid.uuid4().hex
    with _tasks_lock:
        TASKS[task_id] = {"stage": "queued", "percent": 0}
    threading.Thread(
        target=_run_youtube_download,
        args=(task_id, url, translate, translate_filename),
        daemon=True,
    ).start()
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


@app.route("/api/open-location", methods=["POST"])
def api_open_location():
    """Reveal a downloaded file in the OS file manager. Restricted to the
    app's own output folders so an arbitrary path can't be opened."""
    path = (request.get_json(silent=True) or {}).get("path", "")
    if not path:
        return jsonify(error="缺少文件路径"), 400

    abspath = os.path.abspath(path)
    allowed_dirs = [os.path.abspath(d) for d in (YOUTUBE_DOWNLOADS_DIR, SUBTITLE_OUTPUT_DIR, DOWNLOADS_DIR, DUB_OUTPUT_DIR)]
    if not any(abspath.startswith(d + os.sep) for d in allowed_dirs):
        return jsonify(error="非法路径"), 403
    if not os.path.exists(abspath):
        return jsonify(error="文件不存在"), 404

    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(abspath)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", abspath])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(abspath)])
    except Exception as e:
        return jsonify(error=str(e)), 500
    return jsonify(ok=True)


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


@app.route("/api/dub/process", methods=["POST"])
def api_dub_process():
    video_file = request.files.get("video")
    if not video_file or not video_file.filename:
        return jsonify(error="请选择要处理的视频文件"), 400

    try:
        start = float(request.form.get("start", "0"))
        end = float(request.form.get("end", "0"))
    except ValueError:
        return jsonify(error="入点/出点必须是数字"), 400
    if end <= start:
        return jsonify(error="出点必须大于入点"), 400

    try:
        rounds = int(request.form.get("rounds", "20"))
    except ValueError:
        rounds = 20
    rounds = max(1, min(rounds, 40))
    voice = request.form.get("voice", "zh-CN-YunjianNeural").strip() or "zh-CN-YunjianNeural"
    keep_bg = request.form.get("keep_bg", "true") != "false"
    engine = request.form.get("engine", "edge").strip() or "edge"

    # 校验引擎存在且可用（未装/未配 key 的引擎不让选）
    try:
        eng = get_engine(engine)
    except ValueError:
        return jsonify(error=f"未知配音引擎：{engine}"), 400
    if not eng.available():
        return jsonify(error=f"配音引擎「{eng.name}」当前不可用，请先完成安装或配置 Key"), 400

    os.makedirs(DUB_TMP_DIR, exist_ok=True)
    task_id = uuid.uuid4().hex
    video_path = os.path.join(DUB_TMP_DIR, f"{task_id}_{video_file.filename}")
    video_file.save(video_path)

    with _tasks_lock:
        TASKS[task_id] = {"stage": "queued", "percent": 0}
    with _dub_events_lock:
        DUB_EVENTS[task_id] = threading.Event()
    threading.Thread(
        target=_run_dub,
        args=(task_id, video_path, start, end, rounds, voice, keep_bg, engine),
        daemon=True,
    ).start()
    return jsonify(task_id=task_id)


@app.route("/api/dub/confirm", methods=["POST"])
def api_dub_confirm():
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id", "")
    segments = data.get("segments")
    if not task_id or not isinstance(segments, list):
        return jsonify(error="缺少 task_id 或 segments"), 400

    with _dub_events_lock:
        event = DUB_EVENTS.get(task_id)
    if event is None:
        return jsonify(error="任务不存在或已结束"), 404

    # 只接受 text 的改动，时间轴沿用原值（前端回传完整段）
    cleaned = [
        {"start": float(s["start"]), "end": float(s["end"]), "text": str(s.get("text", ""))}
        for s in segments
    ]
    _update_task(task_id, segments=cleaned)
    event.set()  # 放行后续流水线
    return jsonify(ok=True)


@app.route("/api/dub/status/<task_id>")
def api_dub_status(task_id):
    with _tasks_lock:
        task = TASKS.get(task_id)
    if task is None:
        return jsonify(error="任务不存在"), 404
    return jsonify(task)


@app.route("/dubbed_videos/<path:filename>")
def serve_dubbed_video(filename):
    return send_from_directory(DUB_OUTPUT_DIR, filename)


@app.route("/api/dub/engines")
def api_dub_engines():
    """列出配音引擎及可用性，前端据此点亮选项。"""
    return jsonify(engines=list_engines(), configured=configured_providers())


@app.route("/api/dub/voice-config", methods=["POST"])
def api_dub_voice_config():
    """保存某家云端引擎的 Key/配置。body: {provider, fields:{...}}"""
    data = request.get_json(silent=True) or {}
    provider = data.get("provider", "")
    fields = data.get("fields", {})
    if provider not in ("cosyvoice", "iflytek", "volc") or not isinstance(fields, dict):
        return jsonify(error="provider 不合法或缺少 fields"), 400
    # 去掉空值，避免把空字符串覆盖进去
    fields = {k: str(v).strip() for k, v in fields.items() if str(v).strip()}
    if not fields:
        return jsonify(error="没有要保存的配置"), 400
    save_voice_config(provider, fields)
    return jsonify(ok=True)
