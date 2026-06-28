import re
import subprocess

import av
import imageio_ffmpeg


def _format_srt_timestamp(seconds):
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(segments, srt_path):
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, start=1):
            f.write(f"{i}\n")
            f.write(f"{_format_srt_timestamp(seg['start'])} --> {_format_srt_timestamp(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")


def _escape_path_for_filter(path):
    # ffmpeg's filtergraph mini-language treats ':' as an option separator and
    # '\' as its escape char, so Windows paths need both handled before being
    # embedded inside a -vf "subtitles=...” argument.
    escaped = path.replace("\\", "/")
    escaped = escaped.replace(":", "\\:")
    return escaped


_OUT_TIME_RE = re.compile(r"out_time=(\d+):(\d+):(\d+\.\d+)")


def burn_subtitles(video_path, srt_path, output_path, duration, on_progress=None):
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    subtitles_arg = f"subtitles='{_escape_path_for_filter(srt_path)}'"

    process = subprocess.Popen(
        [
            ffmpeg_exe, "-y",
            "-i", video_path,
            "-vf", subtitles_arg,
            "-c:a", "copy",
            "-progress", "pipe:1",
            "-nostats",
            "-loglevel", "error",
            output_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    for line in process.stdout:
        match = _OUT_TIME_RE.search(line)
        if match and on_progress and duration:
            h, m, s = match.groups()
            current = int(h) * 3600 + int(m) * 60 + float(s)
            on_progress(min(current / duration * 100, 100))

    stderr_output = process.stderr.read()
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"字幕烧录失败：{stderr_output[-500:]}")


# ---- ASS 字幕：用于把新台词压在原烧录字幕「上方」 -------------------------

def probe_resolution(video_path):
    """返回 (width, height)。ASS 的 PlayResX/Y 要等于真实分辨率，MarginV 才能
    精确对应像素。"""
    with av.open(video_path) as container:
        stream = next(s for s in container.streams if s.type == "video")
        return stream.codec_context.width, stream.codec_context.height


def _format_ass_timestamp(seconds):
    # ASS 用 H:MM:SS.cc（百分之一秒）
    centis = round(seconds * 100)
    hours, centis = divmod(centis, 360000)
    minutes, centis = divmod(centis, 6000)
    secs, centis = divmod(centis, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _ass_escape_text(text):
    # ASS 里 { } 是override标签起止，换行用 \N；先把它们处理掉
    text = text.replace("{", "(").replace("}", ")")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\n", r"\N")


def write_ass(
    segments,
    ass_path,
    video_width,
    video_height,
    margin_v=None,
    font_size=None,
    font_name="Microsoft YaHei",
    primary_colour="&H0000FFFF",  # 黄色(AABBGGRR)，与原白字幕区分
    outline=2,
):
    """把 segments 写成 ASS 字幕。Alignment=2（底部居中）配较大的 MarginV，
    把字幕顶到原烧录字幕上方。MarginV / 字号默认按分辨率自适应。"""
    if margin_v is None:
        margin_v = int(video_height * 0.12)  # 距底部约12%，落在原字幕上方
    if font_size is None:
        font_size = max(int(video_height * 0.05), 18)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: New,{font_name},{font_size},{primary_colour},&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,{outline},1,2,20,20,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        for seg in segments:
            text = _ass_escape_text((seg.get("text") or "").strip())
            if not text:
                continue
            start = _format_ass_timestamp(seg["start"])
            end = _format_ass_timestamp(seg["end"])
            f.write(f"Dialogue: 0,{start},{end},New,,0,0,0,,{text}\n")


def burn_ass(video_path, ass_path, output_path, duration, on_progress=None):
    """把 ASS 字幕烧进视频。用专门的 ass 滤镜（比 subtitles 滤镜更贴合 ASS
    样式）。音频默认 copy，调用方若已重做音轨可在上层处理。"""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    ass_arg = f"ass='{_escape_path_for_filter(ass_path)}'"

    process = subprocess.Popen(
        [
            ffmpeg_exe, "-y",
            "-i", video_path,
            "-vf", ass_arg,
            "-c:a", "copy",
            "-progress", "pipe:1",
            "-nostats",
            "-loglevel", "error",
            output_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    for line in process.stdout:
        match = _OUT_TIME_RE.search(line)
        if match and on_progress and duration:
            h, m, s = match.groups()
            current = int(h) * 3600 + int(m) * 60 + float(s)
            on_progress(min(current / duration * 100, 100))

    stderr_output = process.stderr.read()
    process.wait()
    if process.returncode != 0:
        raise RuntimeError(f"ASS字幕烧录失败：{stderr_output[-500:]}")
