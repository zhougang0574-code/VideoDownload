"""按入/出点裁出视频片段。重新编码以保证裁切点帧精确（直接 copy 会从最近的
关键帧开始，导致开头多出一截）。"""

import subprocess

import imageio_ffmpeg


def trim(video_path, start, end, out_path, on_progress=None):
    """裁出 [start, end) 秒的片段到 out_path。start/end 单位秒。"""
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    duration = max(end - start, 0.1)
    result = subprocess.run(
        [
            ffmpeg_exe, "-y",
            "-ss", f"{start:.3f}",
            "-i", video_path,
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            out_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"裁剪片段失败：{result.stderr[-300:]}")
    if on_progress:
        on_progress(100)
