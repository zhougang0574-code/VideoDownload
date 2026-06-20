import subprocess

import imageio_ffmpeg
from faster_whisper import WhisperModel

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = WhisperModel("small", device="cpu", compute_type="int8")
    return _model


def extract_audio(video_path, audio_path):
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [
            ffmpeg_exe, "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            audio_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"提取音频失败，视频里可能没有音轨：{result.stderr[-300:]}")


def transcribe(audio_path, on_progress=None):
    """Returns (segments, language, duration). segments is a list of
    {"start": float, "end": float, "text": str}, ordered, covering the whole audio."""
    model = _get_model()
    raw_segments, info = model.transcribe(audio_path, beam_size=5)

    duration = info.duration
    segments = []
    for seg in raw_segments:
        segments.append({"start": seg.start, "end": seg.end, "text": seg.text.strip()})
        if on_progress and duration:
            on_progress(min(seg.end / duration * 100, 100))

    return segments, info.language, duration
