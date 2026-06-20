import os

import imageio_ffmpeg
import yt_dlp

DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "youtube_downloads")


def download_video(url, on_progress=None):
    """Download a YouTube video with yt-dlp, merging best video+audio into mp4.
    Returns (title, output_path)."""
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    def hook(d):
        if not on_progress or d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if total:
            on_progress(d["downloaded_bytes"] / total * 100)

    opts = {
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(DOWNLOADS_DIR, "%(title)s.%(ext)s"),
        "restrictfilenames": False,
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "progress_hooks": [hook],
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        base, _ = os.path.splitext(filepath)
        merged_path = base + ".mp4"
        output_path = merged_path if os.path.exists(merged_path) else filepath
        return info.get("title", "video"), output_path
