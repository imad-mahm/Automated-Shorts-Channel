"""
Upload stage.

Uploads the finished Short to YouTube via the YouTube Data API v3 using a
pre-obtained OAuth2 refresh token (no interactive browser flow at runtime).
Also sets the first scene image as the video thumbnail.
"""

import logging
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

import config

logger = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

CATEGORY_EDUCATION = "27"
CHUNK_SIZE = 1024 * 1024  # 1 MB resumable chunks


def _build_service():
    """
    Build an authenticated YouTube Data API client from env-var credentials.

    Inputs:  none (reads config secrets).
    Output:  a googleapiclient resource for the youtube v3 API.
    """
    credentials = Credentials(
        token=None,  # forces a refresh on first use
        refresh_token=config.YOUTUBE_REFRESH_TOKEN,
        client_id=config.YOUTUBE_CLIENT_ID,
        client_secret=config.YOUTUBE_CLIENT_SECRET,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def _ensure_shorts_description(description: str) -> str:
    """Append #Shorts to the description if it is not already present."""
    if "#shorts" in description.lower():
        return description
    return f"{description}\n\n#Shorts"


def upload_video(video_path: Path, script_data: dict, thumbnail_path: Path | None = None) -> str:
    """
    Upload the Short and (optionally) set its thumbnail.

    Inputs:
        video_path:     path to the final captioned MP4.
        script_data:    the script generator dict (title, description, tags).
        thumbnail_path: optional path to a thumbnail image (scene_01.jpg).
    Output:
        The public YouTube watch URL (str).
    Raises:  RuntimeError on upload failure.
    """
    youtube = _build_service()

    body = {
        "snippet": {
            "title": script_data["title"][:100],
            "description": _ensure_shorts_description(script_data["description"]),
            "tags": script_data.get("tags", []),
            "categoryId": CATEGORY_EDUCATION,
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(Path(video_path).resolve()),
        chunksize=CHUNK_SIZE,
        resumable=True,
        mimetype="video/mp4",
    )

    logger.info("Starting upload: %s", script_data["title"])
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    response = None
    last_logged = 0
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                percent = int(status.progress() * 100)
                # Log roughly every 10%.
                if percent >= last_logged + 10:
                    last_logged = percent - (percent % 10)
                    logger.info("Upload progress: %d%%", percent)
    except HttpError as exc:
        raise RuntimeError(f"YouTube upload failed: {exc}") from exc

    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"Upload returned no video id: {response}")

    logger.info("Upload complete: video id %s", video_id)

    # Thumbnail is uploaded separately after the video exists.
    if thumbnail_path is not None and Path(thumbnail_path).exists():
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(Path(thumbnail_path).resolve())),
            ).execute()
            logger.info("Thumbnail set from %s", Path(thumbnail_path).name)
        except HttpError as exc:
            # Custom thumbnails require a verified channel; don't fail the run.
            logger.warning("Could not set thumbnail (non-fatal): %s", exc)

    url = f"https://www.youtube.com/watch?v={video_id}"
    logger.info("Video URL: %s", url)
    return url
