"""TTS 合成 + 时间轴对齐：把每句鬼畜台词合成中文语音，按 atempo 拉伸到正好
等于原句时长，再拼成一条与原片同时间轴的完整人声轨。

这样画面、字幕、配音三者走同一条时间轴，天然同步。
"""

import asyncio
import os
import subprocess

import edge_tts
import imageio_ffmpeg

from subtitle.voice_engines import get_engine

DEFAULT_VOICE = "zh-CN-YunjianNeural"

# atempo 单次只接受 [0.5, 2.0]，超出要链式串联。这里再设一个总倍率上限，
# 避免译文极长 / 时间槽极短时语音被压成听不清的「电报音」。
TEMPO_MIN = 0.5
TEMPO_MAX = 4.0


def _ffmpeg():
    return imageio_ffmpeg.get_ffmpeg_exe()


def synth_one(text, out_path, voice=DEFAULT_VOICE):
    """合成一句到 out_path（mp3）。edge-tts 是异步的，这里同步封装一下。"""
    async def _run():
        await edge_tts.Communicate(text, voice).save(out_path)

    asyncio.run(_run())


def probe_duration(path):
    """返回音频时长（秒）。用 ffmpeg 跑一遍读 stderr 里的 time，避免额外依赖
    ffprobe（imageio-ffmpeg 只带 ffmpeg）。"""
    result = subprocess.run(
        [_ffmpeg(), "-i", path, "-f", "null", "-"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    duration = 0.0
    for line in result.stderr.splitlines():
        idx = line.find("time=")
        if idx != -1:
            ts = line[idx + 5:].split(" ")[0]
            try:
                h, m, s = ts.split(":")
                duration = int(h) * 3600 + int(m) * 60 + float(s)
            except ValueError:
                pass
    return duration


def _atempo_chain(factor):
    """把任意倍率拆成若干个落在 [0.5, 2.0] 的 atempo 串联，返回 filter 片段，
    如 'atempo=2.0,atempo=1.5'。"""
    factor = max(TEMPO_MIN, min(TEMPO_MAX, factor))
    parts = []
    remaining = factor
    while remaining > 2.0:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.4f}")
    return ",".join(parts)


def fit_to_duration(in_path, target_sec, out_path):
    """把 in_path 的语音拉伸/压缩到约等于 target_sec 秒，输出到 out_path（wav）。

    factor = 实际时长 / 目标时长：实际更长就加速（factor>1），更短就放慢。
    返回实际采用的 factor，便于上层判断是否被倍率上限截断。
    """
    actual = probe_duration(in_path)
    if actual <= 0 or target_sec <= 0:
        factor = 1.0
    else:
        factor = actual / target_sec

    chain = _atempo_chain(factor)
    subprocess.run(
        [
            _ffmpeg(), "-y",
            "-i", in_path,
            "-filter:a", chain,
            "-ar", "44100", "-ac", "2",
            out_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return factor


def slice_reference(clip_audio, start, end, out_path, min_len=6.0, clip_duration=None):
    """从片段原声里切出某句对应的参考音频，供克隆引擎复刻该说话人。

    太短的参考音克隆质量差，所以把窗口在该句两侧扩到至少 min_len 秒（夹在
    片段时长内）。输出单声道 wav。"""
    if clip_duration is None:
        clip_duration = probe_duration(clip_audio)
    seg_len = max(end - start, 0.1)
    pad = max((min_len - seg_len) / 2, 0)
    w_start = max(start - pad, 0)
    w_end = end + pad
    if clip_duration:
        w_end = min(w_end, clip_duration)
    w_len = max(w_end - w_start, 0.5)
    subprocess.run(
        [
            _ffmpeg(), "-y",
            "-ss", f"{w_start:.3f}", "-t", f"{w_len:.3f}",
            "-i", clip_audio,
            "-ar", "22050", "-ac", "1",
            out_path,
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=True,
    )
    return out_path


def synth_segments(segments, out_path, engine="edge", voice=DEFAULT_VOICE,
                   clip_audio=None, work_dir=None, on_progress=None):
    """用指定引擎把每段台词合成语音、按原句时长对齐，拼成完整人声轨写到 out_path。

    engine: 引擎 id（edge/xtts/cosyvoice/...）或 VoiceEngine 实例。克隆引擎会用
    clip_audio 切出每句对应原声做参考，复刻该说话人音色。
    segments: [{"start", "end", "text"}]，时间单位秒。
    """
    eng = get_engine(engine) if isinstance(engine, str) else engine
    work_dir = work_dir or os.path.dirname(os.path.abspath(out_path))
    os.makedirs(work_dir, exist_ok=True)
    timeline_end = max((s["end"] for s in segments), default=0.0)

    clip_duration = probe_duration(clip_audio) if (clip_audio and eng.supports_clone) else None

    # 1) 组装合成任务（克隆引擎附上逐句参考音）
    jobs = []        # 交给引擎合成
    job_meta = []    # 与 jobs 对齐：(start, raw, fitted, target)
    temp_files = []
    for i, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        raw = os.path.join(work_dir, f"_tts_{i}.{eng.output_ext}")
        fitted = os.path.join(work_dir, f"_tts_{i}_fit.wav")
        ref = None
        if eng.supports_clone and clip_audio:
            ref = os.path.join(work_dir, f"_ref_{i}.wav")
            slice_reference(clip_audio, seg["start"], seg["end"], ref, clip_duration=clip_duration)
            temp_files.append(ref)
        jobs.append({"text": text, "out_path": raw, "ref_audio": ref, "voice": voice})
        job_meta.append((seg["start"], raw, fitted, max(seg["end"] - seg["start"], 0.3)))
        temp_files.extend([raw, fitted])

    # 2) 引擎合成（XTTS 等会一次性批处理）
    if jobs:
        eng.synth_batch(jobs, on_progress=on_progress)

    # 3) 逐句拉伸到目标时长，拼成完整人声轨（与引擎无关，复用）
    fitted_paths = []
    for start, raw, fitted, target in job_meta:
        fit_to_duration(raw, target, fitted)
        fitted_paths.append((start, fitted))

    _assemble_track(fitted_paths, timeline_end, out_path)

    for f in temp_files:
        if os.path.exists(f):
            os.remove(f)
    return out_path


def _assemble_track(fitted_paths, total_duration, out_path):
    """在一条 total_duration 秒的静音底轨上，把每段语音按其 start 时间贴进去。

    用 anullsrc 造静音底轨，每个输入 adelay 到对应起点，再 amix 混到一起
    （normalize=0 保持各路原始音量，段落基本不重叠所以不会互相压低）。
    """
    if not fitted_paths:
        # 没有任何语音：产出一段静音，保证下游流程有音轨可用
        subprocess.run(
            [
                _ffmpeg(), "-y",
                "-f", "lavfi", "-t", f"{max(total_duration, 0.1):.3f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                out_path,
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=True,
        )
        return

    cmd = [_ffmpeg(), "-y", "-f", "lavfi", "-t", f"{max(total_duration, 0.1):.3f}",
           "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    for _, path in fitted_paths:
        cmd += ["-i", path]

    filters = []
    labels = ["[0:a]"]  # 静音底轨
    for idx, (start, _) in enumerate(fitted_paths, start=1):
        delay_ms = int(start * 1000)
        filters.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms}[d{idx}]")
        labels.append(f"[d{idx}]")
    n = len(labels)
    filters.append(
        "".join(labels) + f"amix=inputs={n}:normalize=0:dropout_transition=0[out]"
    )

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-t", f"{max(total_duration, 0.1):.3f}",
        "-ar", "44100", "-ac", "2",
        out_path,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=True)


def mix_into_video(video_path, voice_wav, out_path, keep_bg=True, orig_db=-15):
    """把新人声轨合进视频。keep_bg=True 时保留原音（压低 orig_db dB）并叠上
    新配音；False 时直接用新配音替换原音。视频流 copy 不重编码。"""
    ff = _ffmpeg()
    if keep_bg:
        cmd = [
            ff, "-y",
            "-i", video_path,
            "-i", voice_wav,
            "-filter_complex",
            f"[0:a]volume={orig_db}dB[bg];[bg][1:a]amix=inputs=2:duration=first:normalize=0[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            out_path,
        ]
    else:
        cmd = [
            ff, "-y",
            "-i", video_path,
            "-i", voice_wav,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            out_path,
        ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"混音失败：{result.stderr[-400:]}")


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    scratch = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tts_test")
    os.makedirs(scratch, exist_ok=True)

    # 用已知的鬼畜台词 + 假定时间槽，验证「合成 → 拉伸到目标时长 → 拼轨」
    segments = [
        {"start": 0.0, "end": 2.5, "text": "我宁愿教导世界什么是错的，也不愿教导世界什么是错的。"},
        {"start": 2.5, "end": 5.0, "text": "现在世界上唯一的英雄是你的信使和曼西多尔。"},
        {"start": 5.0, "end": 9.0, "text": "真汉子必生于危难之中，拥有三尺剑，才能立下不朽之功。"},
    ]

    print("逐句：自然时长 -> 目标槽 -> 拉伸后")
    for i, seg in enumerate(segments):
        raw = os.path.join(scratch, f"r{i}.mp3")
        fit = os.path.join(scratch, f"r{i}_fit.wav")
        synth_one(seg["text"], raw)
        nat = probe_duration(raw)
        target = seg["end"] - seg["start"]
        factor = fit_to_duration(raw, target, fit)
        got = probe_duration(fit)
        print(f"  句{i}: {nat:5.2f}s -> 目标{target:4.2f}s -> {got:5.2f}s (factor={factor:.2f})")

    track = os.path.join(scratch, "voice_track.wav")
    synth_segments(segments, track, work_dir=scratch)
    print(f"\n完整人声轨: {probe_duration(track):.2f}s（应≈9s）-> {track}")
