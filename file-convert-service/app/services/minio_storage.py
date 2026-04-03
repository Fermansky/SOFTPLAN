import hashlib
import io
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from uuid import uuid4

from minio import Minio
from minio.error import S3Error


def _to_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_extension(extension: str) -> str:
    normalized_extension = extension.lower().strip()
    if normalized_extension and not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"
    return normalized_extension


def _normalize_image_extension(content_type: str) -> str:
    normalized_content_type = content_type.split(";")[0].strip().lower()
    extension_map = {
        "image/avif": ".avif",
        "image/bmp": ".bmp",
        "image/gif": ".gif",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/svg+xml": ".svg",
        "image/tiff": ".tiff",
        "image/webp": ".webp",
    }
    if normalized_content_type in extension_map:
        return extension_map[normalized_content_type]

    guessed = mimetypes.guess_extension(normalized_content_type, strict=False)
    if guessed is None:
        return ""
    if guessed == ".jpe":
        return ".jpg"
    return guessed.lower()


@dataclass(frozen=True)
class StoredObjectRef:
    bucket: str
    storage_key: str


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

    def _resolve_bucket(self, bucket: str | None) -> str:
        return bucket or self.bucket

    def ensure_bucket(self, bucket: str | None = None) -> str:
        resolved_bucket = self._resolve_bucket(bucket)
        if not self.client.bucket_exists(resolved_bucket):
            self.client.make_bucket(resolved_bucket)
        return resolved_bucket

    def upload_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        extension: str = "",
        bucket: str | None = None,
    ) -> str:
        resolved_bucket = self.ensure_bucket(bucket)
        normalized_extension = _normalize_extension(extension)
        storage_key = f"{uuid4()}{normalized_extension}"
        self.client.put_object(
            resolved_bucket,
            storage_key,
            io.BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )
        return storage_key

    def upload_document_bytes(self, payload: bytes, *, content_type: str, extension: str = "") -> StoredObjectRef:
        resolved_bucket = self.ensure_bucket()
        normalized_extension = _normalize_extension(extension)
        now = datetime.now(timezone.utc)
        storage_key = f"documents/{now:%Y/%m}/{uuid4()}{normalized_extension}"
        self.client.put_object(
            resolved_bucket,
            storage_key,
            io.BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )
        return StoredObjectRef(bucket=resolved_bucket, storage_key=storage_key)

    def upload_image_bytes(self, payload: bytes, *, content_type: str) -> StoredObjectRef:
        resolved_bucket = self.ensure_bucket()
        extension = _normalize_image_extension(content_type)
        payload_hash = hashlib.sha256(payload).hexdigest()
        storage_key = f"images/{payload_hash}{extension}"
        if not self.object_exists(storage_key, bucket=resolved_bucket):
            self.client.put_object(
                resolved_bucket,
                storage_key,
                io.BytesIO(payload),
                length=len(payload),
                content_type=content_type,
            )
        return StoredObjectRef(bucket=resolved_bucket, storage_key=storage_key)

    def remove_object(self, storage_key: str, *, bucket: str | None = None) -> None:
        resolved_bucket = self._resolve_bucket(bucket)
        self.client.remove_object(resolved_bucket, storage_key)

    def download_bytes(self, storage_key: str, *, bucket: str | None = None) -> bytes:
        resolved_bucket = self._resolve_bucket(bucket)
        response = self.client.get_object(resolved_bucket, storage_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def object_exists(self, storage_key: str, *, bucket: str | None = None) -> bool:
        resolved_bucket = self._resolve_bucket(bucket)
        try:
            self.client.stat_object(resolved_bucket, storage_key)
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
