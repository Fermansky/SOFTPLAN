import os
from functools import lru_cache

import httpx


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

    def convert_pdf_to_markdown(self, *, storage_key: str) -> tuple[str | None, str | None]:
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

        markdown = payload.get("markdown") if isinstance(payload, dict) else None
        if not isinstance(markdown, str):
            return None, f"Unexpected convert response payload: {payload!r}"
        return markdown, None


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
