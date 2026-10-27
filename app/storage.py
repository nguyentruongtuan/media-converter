import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from minio import Minio
from minio.datatypes import PostPolicy
from minio.deleteobjects import DeleteObject
from minio.error import S3Error

from app.config import Settings, get_settings
from app.media_types import cache_control_for, content_type_for

logger = logging.getLogger(__name__)


@dataclass
class ObjectStat:
    size: int
    content_type: str | None
    etag: str | None


@dataclass
class PresignedUpload:
    method: str
    url: str
    fields: dict[str, str]
    put_url: str
    expires_at: datetime


def public_read_policy(bucket: str) -> str:
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/*"],
                }
            ],
        }
    )


class StorageService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.source_bucket = settings.source_bucket
        self.output_bucket = settings.output_bucket
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region=settings.minio_region,
        )
        self.public_endpoint = settings.minio_public_endpoint or settings.minio_endpoint
        self.public_secure = (
            settings.minio_secure if settings.minio_public_secure is None else settings.minio_public_secure
        )
        # Region is fixed so presigning never needs a network round-trip to the public host.
        self.public_client = Minio(
            self.public_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=self.public_secure,
            region=settings.minio_region,
        )

    def ensure_buckets(self) -> None:
        for bucket in (self.source_bucket, self.output_bucket):
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)
                logger.info("Created bucket", extra={"bucket": bucket})
        if self.settings.output_bucket_public_read:
            self.client.set_bucket_policy(self.output_bucket, public_read_policy(self.output_bucket))
        else:
            self._delete_policy(self.output_bucket)
        # Originals are never anonymously readable.
        self._delete_policy(self.source_bucket)

    def _delete_policy(self, bucket: str) -> None:
        try:
            self.client.delete_bucket_policy(bucket)
        except S3Error as exc:
            if exc.code not in ("NoSuchBucketPolicy",):
                raise

    def ping(self) -> None:
        self.client.bucket_exists(self.source_bucket)

    def presigned_upload(self, key: str, content_type: str, max_bytes: int) -> PresignedUpload:
        expiry = timedelta(seconds=self.settings.presigned_upload_expiry_seconds)
        expires_at = datetime.now(UTC) + expiry
        policy = PostPolicy(self.source_bucket, expires_at)
        policy.add_equals_condition("key", key)
        policy.add_equals_condition("Content-Type", content_type)
        policy.add_content_length_range_condition(1, max_bytes)
        fields = self.public_client.presigned_post_policy(policy)
        fields = {"key": key, "Content-Type": content_type, **fields}
        scheme = "https" if self.public_secure else "http"
        put_url = self.public_client.presigned_put_object(self.source_bucket, key, expires=expiry)
        return PresignedUpload(
            method="POST",
            url=f"{scheme}://{self.public_endpoint}/{self.source_bucket}",
            fields=fields,
            put_url=put_url,
            expires_at=expires_at,
        )

    def presigned_download(self, bucket: str, key: str, *, public: bool = True) -> str:
        client = self.public_client if public else self.client
        return client.presigned_get_object(
            bucket, key, expires=timedelta(seconds=self.settings.presigned_download_expiry_seconds)
        )

    def stat(self, bucket: str, key: str) -> ObjectStat | None:
        try:
            info = self.client.stat_object(bucket, key)
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject", "NotFound"):
                return None
            raise
        return ObjectStat(size=info.size, content_type=info.content_type, etag=info.etag)

    def download_file(self, bucket: str, key: str, path: Path) -> None:
        self.client.fget_object(bucket, key, str(path))

    def upload_file(self, bucket: str, key: str, path: Path) -> None:
        self.client.fput_object(
            bucket,
            key,
            str(path),
            content_type=content_type_for(key),
            metadata={"Cache-Control": cache_control_for(key)},
        )

    def delete_object(self, bucket: str, key: str) -> None:
        self.client.remove_object(bucket, key)

    def delete_prefix(self, bucket: str, prefix: str) -> int:
        objects = [
            DeleteObject(o.object_name)
            for o in self.client.list_objects(bucket, prefix=prefix, recursive=True)
        ]
        if not objects:
            return 0
        errors = list(self.client.remove_objects(bucket, objects))
        if errors:
            raise RuntimeError(f"Failed to delete {len(errors)} objects under {bucket}/{prefix}")
        return len(objects)


@lru_cache
def get_storage() -> StorageService:
    return StorageService(get_settings())
