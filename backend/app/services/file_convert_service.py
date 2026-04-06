"""file-convert-service 调用客户端。"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx

from ..core.logging import REQUEST_ID_HEADER, get_request_id


@dataclass(frozen=True)
class UploadedImageMetadata:
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
    markdown: str
    image_hashes: dict[str, str]
    uploaded_images: list[UploadedImageMetadata] = field(default_factory=list)


class FileConvertServiceClient:
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
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"Unexpected convert response payload: {payload!r}")
        return value

    def _validate_optional_int(self, value: Any, payload: dict[str, Any]) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Unexpected convert response payload: {payload!r}")
        return value

    def _parse_uploaded_images(self, payload: dict[str, Any]) -> list[UploadedImageMetadata]:
        uploaded_images_payload = payload.get("uploaded_images", [])
        if not isinstance(uploaded_images_payload, list):
            raise ValueError(f"Unexpected convert response payload: {payload!r}")

        uploaded_images: list[UploadedImageMetadata] = []
        for item in uploaded_images_payload:
            if not isinstance(item, dict):
                raise ValueError(f"Unexpected convert response payload: {payload!r}")

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
    ) -> tuple[PdfToMarkdownResult | None, str | None]:
        convert_url = f"{self.base_url}/internal/converters/pdf-to-markdown"
        resolved_request_id = get_request_id() or task_id
        request_headers: dict[str, str] | None = None
        if resolved_request_id is not None:
            request_headers = {REQUEST_ID_HEADER: resolved_request_id}
        if task_id is not None:
            request_headers = request_headers or {}
            request_headers["X-Convert-Task-Id"] = task_id

        try:
            response = httpx.post(
                convert_url,
                json={"storage_key": storage_key},
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
    base_url = os.getenv("FILE_CONVERT_SERVICE_BASE_URL", "http://file-convert-service:8000")
    timeout_seconds = float(os.getenv("FILE_CONVERT_SERVICE_TIMEOUT_SECONDS", "3"))
    convert_timeout_seconds = float(os.getenv("FILE_CONVERT_SERVICE_CONVERT_TIMEOUT_SECONDS", "120"))
    return FileConvertServiceClient(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        convert_timeout_seconds=convert_timeout_seconds,
    )
