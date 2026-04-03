"""MinIO 存储服务。

职责：
1. 统一封装对象存储的上传、下载、删除、存在性检查。
2. 约定文档与图片对象的 key 规则：
   - 文档：documents/{yyyy}/{mm}/{uuid}{ext}
   - 图片：images/{sha256}{ext}
3. 对图片上传执行“内容哈希去重”：同内容仅首次写入，后续直接复用。

说明：
- 本模块不承载业务鉴权，仅提供存储能力。
- 默认使用单桶策略，通过 key 前缀区分业务对象类型。
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
    """规范化扩展名，确保小写并以点号开头。"""
    normalized_extension = extension.lower().strip()
    if normalized_extension and not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"
    return normalized_extension


def _normalize_image_extension(content_type: str) -> str:
    """根据 MIME 类型推导图片扩展名。

    约束：
    - 优先使用显式映射，保证常见图片类型结果稳定。
    - 未命中映射时，降级使用 `mimetypes.guess_extension`。
    - 无法推导时返回空字符串，调用方仍可继续上传。
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
    """对象存储定位信息。"""

    bucket: str
    storage_key: str


class MinioStorage:
    """MinIO 客户端的业务友好封装。"""

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
        """解析最终使用的桶名（显式参数优先，默认桶兜底）。"""
        return bucket or self.bucket

    def ensure_bucket(self, bucket: str | None = None) -> str:
        """确保目标桶存在，不存在则创建后返回桶名。"""
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
        """通用二进制上传接口，使用随机 key。"""
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

        key 规则：`documents/{yyyy}/{mm}/{uuid}{ext}`，便于按时间分段与人工排查。
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
        """上传图片对象并按内容去重。

        约束：
        - 以实际上传字节计算 sha256，保证跨文档全局复用。
        - key 规则：`images/{sha256}{ext}`。
        - 若对象已存在，不重复写入，直接返回定位信息。
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
        """删除对象（调用方负责保证删除语义安全）。"""
        resolved_bucket = self._resolve_bucket(bucket)
        self.client.remove_object(resolved_bucket, storage_key)

    def download_bytes(self, storage_key: str, *, bucket: str | None = None) -> bytes:
        """下载对象完整字节内容。"""
        resolved_bucket = self._resolve_bucket(bucket)
        response = self.client.get_object(resolved_bucket, storage_key)
        try:
            return response.read()
        finally:
            # MinIO SDK 要求显式释放连接，避免连接池泄漏。
            response.close()
            response.release_conn()

    def object_exists(self, storage_key: str, *, bucket: str | None = None) -> bool:
        """判断对象是否存在。

        约束：
        - 对“对象不存在”类错误返回 False。
        - 其他异常保持抛出，让上层按错误语义处理。
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
    """读取环境变量并构造 MinIO 存储单例。"""
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

