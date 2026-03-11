import io
import os
from functools import lru_cache
from uuid import uuid4

from minio import Minio
from minio.error import S3Error


def _to_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class MinioStorage:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        self.bucket = bucket
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def upload_bytes(self, payload: bytes, *, content_type: str, extension: str = "") -> str:
        self.ensure_bucket()
        normalized_extension = extension.lower()
        if normalized_extension and not normalized_extension.startswith("."):
            normalized_extension = f".{normalized_extension}"
        storage_key = f"{uuid4()}{normalized_extension}"
        self.client.put_object(
            self.bucket,
            storage_key,
            io.BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )
        return storage_key

    def remove_object(self, storage_key: str) -> None:
        self.client.remove_object(self.bucket, storage_key)

    def object_exists(self, storage_key: str) -> bool:
        try:
            self.client.stat_object(self.bucket, storage_key)
            return True
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}:
                return False
            raise


@lru_cache(maxsize=1)
def get_minio_storage() -> MinioStorage:
    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:10000")
    access_key = os.getenv("MINIO_ACCESS_KEY") or os.getenv("MINIO_ROOT_USER", "minioadmin")
    secret_key = os.getenv("MINIO_SECRET_KEY") or os.getenv("MINIO_ROOT_PASSWORD", "minioadmin")
    bucket = os.getenv("MINIO_BUCKET", "softplan")
    secure = _to_bool(os.getenv("MINIO_SECURE"), default=False)
    return MinioStorage(
        endpoint=endpoint,
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
        secure=secure,
    )
