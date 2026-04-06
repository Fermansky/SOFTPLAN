"""file-convert-service 调用客户端。

职责：
1. 统一封装 backend 到 file-convert-service 的健康检查与 PDF 转 markdown 请求。
2. 校验下游返回结构，并转换为 backend 内部稳定的数据结构。
3. 透传任务级 request id 与可选模型参数，便于异步链路追踪。

说明：
- 本模块只负责 HTTP 协议适配，不负责文档解析任务状态流转。
- 下游调用失败统一返回 `(None, error)`，由上层服务决定如何映射为任务失败或 HTTP 错误。
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx

from ..core.logging import REQUEST_ID_HEADER, get_request_id


@dataclass(frozen=True)
class UploadedImageMetadata:
    """file-convert-service 返回的单张图片元数据。"""

    source_key: str
    file_hash: str
    storage_bucket: str
    storage_key: str
    file_size: int
    content_type: str
    extension: str | None
    width: int | None
    height: int | None


@dataclass(frozen=True)
class PdfToMarkdownResult:
    """PDF 转 markdown 的标准化结果。"""

    markdown: str
    image_hashes: dict[str, str]
    uploaded_images: list[UploadedImageMetadata] = field(default_factory=list)


class FileConvertServiceClient:
    """file-convert-service 轻量客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 3.0,
        convert_timeout_seconds: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.convert_timeout_seconds = convert_timeout_seconds

    def check_availability(self) -> tuple[bool, str | None]:
        """检查 file-convert-service 健康状态。"""
        health_url = f"{self.base_url}/health"
        request_id = get_request_id()
        request_headers = {REQUEST_ID_HEADER: request_id} if request_id is not None else None
        try:
            response = httpx.get(health_url, headers=request_headers, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return False, str(exc)

        status_value = payload.get("status") if isinstance(payload, dict) else None
        if status_value != "ok":
            return False, f"Unexpected health payload: {payload!r}"
        return True, None

    def _validate_optional_str(self, value: Any, payload: dict[str, Any]) -> str | None:
        """校验下游可选字符串字段。"""
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Unexpected convert response payload: {payload!r}")
        return value

    def _validate_optional_int(self, value: Any, payload: dict[str, Any]) -> int | None:
        """校验下游可选整数值字段。"""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Unexpected convert response payload: {payload!r}")
        return value

    def _parse_uploaded_images(self, payload: dict[str, Any]) -> list[UploadedImageMetadata]:
        """解析并校验下游返回的图片元数据列表。

        失败语义：
        - 任意字段类型不符合预期时抛出 `ValueError`，避免半结构化数据进入上层流程。
        """
        uploaded_images_payload = payload.get("uploaded_images", [])
        if not isinstance(uploaded_images_payload, list):
            raise ValueError(f"Unexpected convert response payload: {payload!r}")

        uploaded_images: list[UploadedImageMetadata] = []
        for item in uploaded_images_payload:
            if not isinstance(item, dict):
                raise ValueError(f"Unexpected convert response payload: {payload!r}")

            # 显式逐字段校验，便于在下游协议漂移时第一时间发现问题。
            source_key = item.get("source_key")
            file_hash = item.get("file_hash")
            storage_bucket = item.get("storage_bucket")
            storage_key = item.get("storage_key")
            file_size = item.get("file_size")
            content_type = item.get("content_type")

            if not isinstance(source_key, str):
                raise ValueError(f"Unexpected convert response payload: {payload!r}")
            if not isinstance(file_hash, str):
                raise ValueError(f"Unexpected convert response payload: {payload!r}")
            if not isinstance(storage_bucket, str):
                raise ValueError(f"Unexpected convert response payload: {payload!r}")
            if not isinstance(storage_key, str):
                raise ValueError(f"Unexpected convert response payload: {payload!r}")
            if isinstance(file_size, bool) or not isinstance(file_size, int):
                raise ValueError(f"Unexpected convert response payload: {payload!r}")
            if not isinstance(content_type, str):
                raise ValueError(f"Unexpected convert response payload: {payload!r}")

            uploaded_images.append(
                UploadedImageMetadata(
                    source_key=source_key,
                    file_hash=file_hash,
                    storage_bucket=storage_bucket,
                    storage_key=storage_key,
                    file_size=file_size,
                    content_type=content_type,
                    extension=self._validate_optional_str(item.get("extension"), payload),
                    width=self._validate_optional_int(item.get("width"), payload),
                    height=self._validate_optional_int(item.get("height"), payload),
                )
            )

        return uploaded_images

    def convert_pdf_to_markdown(
        self,
        *,
        storage_key: str,
        task_id: str | None = None,
        model: str | None = None,
    ) -> tuple[PdfToMarkdownResult | None, str | None]:
        """调用 file-convert-service 执行 PDF 转 markdown。

        副作用：
        - 会发起 HTTP 请求到 file-convert-service。

        失败语义：
        - 网络错误、HTTP 错误、下游响应结构错误统一返回 `(None, error)`。
        """
        convert_url = f"{self.base_url}/internal/converters/pdf-to-markdown"
        resolved_request_id = get_request_id() or task_id
        request_headers: dict[str, str] | None = None
        if resolved_request_id is not None:
            request_headers = {REQUEST_ID_HEADER: resolved_request_id}
        if task_id is not None:
            # 独立保留任务 id 头，方便下游在异步链路中定位顶层任务。
            request_headers = request_headers or {}
            request_headers["X-Convert-Task-Id"] = task_id

        request_payload: dict[str, Any] = {"storage_key": storage_key}
        if model is not None:
            # 仅在上层显式解析出目标模型时透传，空值表示沿用下游默认行为。
            request_payload["model"] = model

        try:
            response = httpx.post(
                convert_url,
                json=request_payload,
                headers=request_headers,
                timeout=self.convert_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return None, str(exc)

        if not isinstance(payload, dict):
            return None, f"Unexpected convert response payload: {payload!r}"

        markdown = payload.get("markdown")
        if not isinstance(markdown, str):
            return None, f"Unexpected convert response payload: {payload!r}"

        image_hashes = payload.get("image_hashes", {})
        if not isinstance(image_hashes, dict):
            return None, f"Unexpected convert response payload: {payload!r}"

        normalized_image_hashes: dict[str, str] = {}
        for key, value in image_hashes.items():
            # 转为稳定的 `dict[str, str]`，后续任务服务才能安全写回 JSON 字段。
            if not isinstance(key, str) or not isinstance(value, str):
                return None, f"Unexpected convert response payload: {payload!r}"
            normalized_image_hashes[key] = value

        try:
            uploaded_images = self._parse_uploaded_images(payload)
        except ValueError as exc:
            return None, str(exc)

        return PdfToMarkdownResult(
            markdown=markdown,
            image_hashes=normalized_image_hashes,
            uploaded_images=uploaded_images,
        ), None


@lru_cache(maxsize=1)
def get_file_convert_service_client() -> FileConvertServiceClient:
    """按环境变量构造 file-convert-service 客户端单例。"""
    base_url = os.getenv("FILE_CONVERT_SERVICE_BASE_URL", "http://file-convert-service:8000")
    timeout_seconds = float(os.getenv("FILE_CONVERT_SERVICE_TIMEOUT_SECONDS", "3"))
    convert_timeout_seconds = float(os.getenv("FILE_CONVERT_SERVICE_CONVERT_TIMEOUT_SECONDS", "120"))
    return FileConvertServiceClient(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        convert_timeout_seconds=convert_timeout_seconds,
    )
