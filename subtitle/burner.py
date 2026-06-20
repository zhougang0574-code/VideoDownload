import re
import subprocess

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
