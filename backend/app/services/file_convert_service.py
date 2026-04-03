import os
from dataclasses import dataclass
from functools import lru_cache

import httpx


@dataclass(frozen=True)
class PdfToMarkdownResult:
    markdown: str
    image_hashes: dict[str, str]


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
        try:
            response = httpx.get(health_url, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return False, str(exc)

        status_value = payload.get("status") if isinstance(payload, dict) else None
        if status_value != "ok":
            return False, f"Unexpected health payload: {payload!r}"
        return True, None

    def convert_pdf_to_markdown(self, *, storage_key: str) -> tuple[PdfToMarkdownResult | None, str | None]:
        convert_url = f"{self.base_url}/internal/converters/pdf-to-markdown"
        try:
            response = httpx.post(
                convert_url,
                json={"storage_key": storage_key},
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

        return PdfToMarkdownResult(markdown=markdown, image_hashes=normalized_image_hashes), None


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
