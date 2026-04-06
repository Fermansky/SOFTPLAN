"""MinIO 存储适配服务。

职责：
1. 统一封装 backend 对 MinIO 的上传、下载、删除与存在性检查。
2. 维护文档对象与图片对象的存储 key 规则。
3. 为上层服务提供稳定的单桶访问入口与对象引用结构。

说明：
- 本模块只负责对象存储协议适配，不负责数据库记录的创建与修复。
- 文档对象使用 `documents/{yyyy}/{mm}/{uuid}{ext}`，图片对象使用 `images/{sha256}{ext}`。
- 图片上传采用 sha256 去重；文档上传始终生成新 key，由上层决定是否复用 FileRecord。
"""

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
    """将环境变量字符串解析为布尔值。"""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_extension(extension: str) -> str:
    """规范化扩展名，保证非空扩展名前带有点号。"""
    normalized_extension = extension.lower().strip()
    if normalized_extension and not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"
    return normalized_extension


def _normalize_image_extension(content_type: str) -> str:
    """根据图片 MIME 类型推导稳定扩展名。

    约束：
    - 优先使用显式映射，避免不同平台对同一 MIME 的推导结果不一致。
    - 无法识别时回退到 `mimetypes.guess_extension`。
    - 仍无法推导时返回空字符串，由上层继续使用无扩展名 key。
    """
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
    """MinIO 中已存储对象的稳定引用。"""

    bucket: str
    storage_key: str


class MinioStorage:
    """MinIO 轻量客户端封装。"""

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
        """解析实际访问的桶名，未显式指定时回退到默认桶。"""
        return bucket or self.bucket

    def ensure_bucket(self, bucket: str | None = None) -> str:
        """确保目标桶存在。

        副作用：
        - 当桶不存在时会直接创建桶。
        """
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
        """上传任意字节内容，并返回生成的对象 key。

        说明：
        - 该接口只负责生成随机对象名，不提供去重语义。
        - 上层如果需要稳定 key 或复用策略，应调用更具体的上传接口。
        """
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
        """上传文档对象。

        约束：
        - 文档对象总是生成新 key，不在存储层做去重复用。
        - key 采用按年月分层的目录结构，便于后续运维排查与清理。
        """
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
        """上传图片对象，并按内容哈希做 Dedup Reuse。

        约束：
        - key 固定为 `images/{sha256}{ext}`。
        - 若同一内容对象已存在，则直接复用，不重复写入 MinIO。
        - 去重仅基于原始字节内容，不比较业务元数据。
        """
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
        """删除指定对象。"""
        resolved_bucket = self._resolve_bucket(bucket)
        self.client.remove_object(resolved_bucket, storage_key)

    def download_bytes(self, storage_key: str, *, bucket: str | None = None) -> bytes:
        """下载对象内容并返回完整字节串。

        副作用：
        - 始终在 finally 中关闭 MinIO 响应流，避免连接泄漏。
        """
        resolved_bucket = self._resolve_bucket(bucket)
        response = self.client.get_object(resolved_bucket, storage_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def object_exists(self, storage_key: str, *, bucket: str | None = None) -> bool:
        """检查对象是否存在。

        失败语义：
        - 仅缺失类错误返回 False。
        - 其他 MinIO 错误继续抛出，由上层决定 Fail-safe 策略。
        """
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
    """按环境变量构造 MinIO 存储单例。"""
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
