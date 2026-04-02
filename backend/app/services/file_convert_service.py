import os
from functools import lru_cache

import httpx


class FileConvertServiceClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

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


@lru_cache(maxsize=1)
def get_file_convert_service_client() -> FileConvertServiceClient:
    base_url = os.getenv("FILE_CONVERT_SERVICE_BASE_URL", "http://file-convert-service:8000")
    return FileConvertServiceClient(base_url=base_url, timeout_seconds=3.0)
