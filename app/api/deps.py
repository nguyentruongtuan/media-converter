from collections.abc import Callable

from app.services import ffprobe
from app.storage import StorageService, get_storage
from app.worker.dispatch import enqueue_transcode


def get_storage_dep() -> StorageService:
    return get_storage()


def get_prober() -> Callable:
    return ffprobe.probe


def get_enqueue() -> Callable[..., bool]:
    return enqueue_transcode
