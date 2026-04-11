"""file-convert-service HTTP client adapters."""

import base64
import hashlib
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

import httpx

from ..core.logging import REQUEST_ID_HEADER, get_request_id


@dataclass(frozen=True)
class UploadedImageMetadata:
    """Uploaded image metadata returned by the storage-key API."""

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
class InlineImageData:
    """Inline image payload returned by the file-upload API."""

    source_key: str
    file_hash: str
    payload: bytes
    file_size: int
    content_type: str
    extension: str | None
    width: int | None
    height: int | None


@dataclass(frozen=True)
class PdfToMarkdownResult:
    """Normalized PDF-to-markdown result."""

    markdown: str
    image_hashes: dict[str, str]
    inline_images: list[InlineImageData] = field(default_factory=list)
    uploaded_images: list[UploadedImageMetadata] = field(default_factory=list)


class FileConvertServiceClient:
    """Thin HTTP client for file-convert-service."""

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
        """Check file-convert-service health."""
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

    def _validate_required_str(self, value: Any, payload: dict[str, Any]) -> str:
        if not isinstance(value, str):
            raise ValueError(f"Unexpected convert response payload: {payload!r}")
        return value

    def _validate_required_int(self, value: Any, payload: dict[str, Any]) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Unexpected convert response payload: {payload!r}")
        return value

    def _parse_image_hashes(self, payload: dict[str, Any]) -> dict[str, str]:
        image_hashes = payload.get("image_hashes", {})
        if not isinstance(image_hashes, dict):
            raise ValueError(f"Unexpected convert response payload: {payload!r}")

        normalized_image_hashes: dict[str, str] = {}
        for key, value in image_hashes.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError(f"Unexpected convert response payload: {payload!r}")
            normalized_image_hashes[key] = value
        return normalized_image_hashes

    def _parse_uploaded_images(self, payload: dict[str, Any]) -> list[UploadedImageMetadata]:
        uploaded_images_payload = payload.get("uploaded_images", [])
        if not isinstance(uploaded_images_payload, list):
            raise ValueError(f"Unexpected convert response payload: {payload!r}")

        uploaded_images: list[UploadedImageMetadata] = []
        for item in uploaded_images_payload:
            if not isinstance(item, dict):
                raise ValueError(f"Unexpected convert response payload: {payload!r}")

            source_key = self._validate_required_str(item.get("source_key"), payload)
            file_hash = self._validate_required_str(item.get("file_hash"), payload)
            storage_bucket = self._validate_required_str(item.get("storage_bucket"), payload)
            storage_key = self._validate_required_str(item.get("storage_key"), payload)
            file_size = self._validate_required_int(item.get("file_size"), payload)
            content_type = self._validate_required_str(item.get("content_type"), payload)

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

    def _parse_inline_images(
        self,
        payload: dict[str, Any],
        *,
        image_hashes: dict[str, str],
    ) -> list[InlineImageData]:
        inline_images_payload = payload.get("images", [])
        if not isinstance(inline_images_payload, list):
            raise ValueError(f"Unexpected convert response payload: {payload!r}")

        inline_images: list[InlineImageData] = []
        for item in inline_images_payload:
            if not isinstance(item, dict):
                raise ValueError(f"Unexpected convert response payload: {payload!r}")

            source_key = self._validate_required_str(item.get("source_key"), payload)
            file_hash = self._validate_required_str(item.get("file_hash"), payload)
            file_size = self._validate_required_int(item.get("file_size"), payload)
            content_type = self._validate_required_str(item.get("content_type"), payload)
            content_base64 = self._validate_required_str(item.get("content_base64"), payload)

            try:
                inline_payload = base64.b64decode(content_base64.encode("ascii"), validate=True)
            except Exception as exc:
                raise ValueError(f"Unexpected convert response payload: {payload!r}") from exc

            if len(inline_payload) != file_size:
                raise ValueError(f"Unexpected convert response payload: {payload!r}")

            payload_hash = hashlib.sha256(inline_payload).hexdigest()
            if payload_hash != file_hash:
                raise ValueError(f"Unexpected convert response payload: {payload!r}")

            expected_hash = image_hashes.get(source_key)
            if expected_hash is not None and expected_hash != file_hash:
                raise ValueError(f"Unexpected convert response payload: {payload!r}")

            inline_images.append(
                InlineImageData(
                    source_key=source_key,
                    file_hash=file_hash,
                    payload=inline_payload,
                    file_size=file_size,
                    content_type=content_type,
                    extension=self._validate_optional_str(item.get("extension"), payload),
                    width=self._validate_optional_int(item.get("width"), payload),
                    height=self._validate_optional_int(item.get("height"), payload),
                )
            )

        return inline_images

    def convert_pdf_to_markdown(
        self,
        *,
        storage_key: str,
        task_id: str | None = None,
        model: str | None = None,
    ) -> tuple[PdfToMarkdownResult | None, str | None]:
        """Call the legacy storage-key API."""
        convert_url = f"{self.base_url}/internal/converters/pdf-to-markdown"
        resolved_request_id = get_request_id() or task_id
        request_headers: dict[str, str] | None = None
        if resolved_request_id is not None:
            request_headers = {REQUEST_ID_HEADER: resolved_request_id}
        if task_id is not None:
            request_headers = request_headers or {}
            request_headers["X-Convert-Task-Id"] = task_id

        request_payload: dict[str, Any] = {"storage_key": storage_key}
        if model is not None:
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

        try:
            normalized_image_hashes = self._parse_image_hashes(payload)
            uploaded_images = self._parse_uploaded_images(payload)
        except ValueError as exc:
            return None, str(exc)

        return PdfToMarkdownResult(
            markdown=markdown,
            image_hashes=normalized_image_hashes,
            uploaded_images=uploaded_images,
        ), None

    def convert_pdf_to_markdown_from_file(
        self,
        *,
        filename: str,
        payload: bytes,
        task_id: str | None = None,
        model: str | None = None,
    ) -> tuple[PdfToMarkdownResult | None, str | None]:
        """Call the file-upload API and receive inline images."""
        convert_url = f"{self.base_url}/internal/converters/pdf-to-markdown/file"
        resolved_request_id = get_request_id() or task_id
        request_headers: dict[str, str] | None = None
        if resolved_request_id is not None:
            request_headers = {REQUEST_ID_HEADER: resolved_request_id}
        if task_id is not None:
            request_headers = request_headers or {}
            request_headers["X-Convert-Task-Id"] = task_id

        normalized_filename = PurePosixPath(filename.strip() or "document.pdf").name or "document.pdf"
        request_data: dict[str, str] | None = None
        if model is not None:
            request_data = {"model": model}

        try:
            response = httpx.post(
                convert_url,
                data=request_data,
                files={"file": (normalized_filename, payload, "application/pdf")},
                headers=request_headers,
                timeout=self.convert_timeout_seconds,
            )
            response.raise_for_status()
            response_payload = response.json()
        except Exception as exc:
            return None, str(exc)

        if not isinstance(response_payload, dict):
            return None, f"Unexpected convert response payload: {response_payload!r}"

        markdown = response_payload.get("markdown")
        if not isinstance(markdown, str):
            return None, f"Unexpected convert response payload: {response_payload!r}"

        try:
            normalized_image_hashes = self._parse_image_hashes(response_payload)
            inline_images = self._parse_inline_images(response_payload, image_hashes=normalized_image_hashes)
        except ValueError as exc:
            return None, str(exc)

        return PdfToMarkdownResult(
            markdown=markdown,
            image_hashes=normalized_image_hashes,
            inline_images=inline_images,
        ), None


@lru_cache(maxsize=1)
def get_file_convert_service_client() -> FileConvertServiceClient:
    """Build the singleton client from environment variables."""
    base_url = os.getenv("FILE_CONVERT_SERVICE_BASE_URL", "http://file-convert-service:8000")
    timeout_seconds = float(os.getenv("FILE_CONVERT_SERVICE_TIMEOUT_SECONDS", "3"))
    convert_timeout_seconds = float(os.getenv("FILE_CONVERT_SERVICE_CONVERT_TIMEOUT_SECONDS", "120"))
    return FileConvertServiceClient(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        convert_timeout_seconds=convert_timeout_seconds,
    )
