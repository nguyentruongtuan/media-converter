from pathlib import PurePath

CONTENT_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

# Playlists may be re-written by a later retry of the same job, segments never change.
CACHE_CONTROL = {
    ".m3u8": "public, max-age=60",
}
DEFAULT_CACHE_CONTROL = "public, max-age=31536000, immutable"


def content_type_for(path: str) -> str:
    return CONTENT_TYPES.get(PurePath(path).suffix.lower(), "application/octet-stream")


def cache_control_for(path: str) -> str:
    return CACHE_CONTROL.get(PurePath(path).suffix.lower(), DEFAULT_CACHE_CONTROL)
