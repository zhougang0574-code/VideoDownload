from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def upload_video_file(filepath, title, desc, tags, privacy, credentials, on_progress=None):
    """Upload a local video file to the authenticated user's YouTube channel.
    Returns the new video's id."""
    youtube = build("youtube", "v3", credentials=credentials)

    body = {
        "snippet": {
            "title": title,
            "description": desc,
            "tags": [t.strip() for t in tags.split(",") if t.strip()],
        },
        "status": {"privacyStatus": privacy},
    }
    media = MediaFileUpload(filepath, chunksize=4 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status and on_progress:
            on_progress(status.progress() * 100)

    return response["id"]
