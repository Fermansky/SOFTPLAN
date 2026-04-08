"""Backend compatibility proxy used by llm-service."""

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from ..core.logging import REQUEST_ID_HEADER, build_log_extra

logger = logging.getLogger(__name__)
CALLER_SERVICE_HEADER = "X-Caller-Service"


@dataclass(frozen=True)
class BackendProxyConfig:
    base_url: str
    timeout_seconds: float


class BackendProxyError(RuntimeError):
    """Raised when llm-service cannot reach backend compatibility routes."""


def load_backend_proxy_config() -> BackendProxyConfig:
    return BackendProxyConfig(
        base_url=os.getenv("BACKEND_BASE_URL", "http://api:8000").rstrip("/"),
        timeout_seconds=float(os.getenv("BACKEND_PROXY_TIMEOUT_SECONDS", "30")),
    )


def log_backend_proxy_config(config: BackendProxyConfig | None = None) -> None:
    resolved_config = config or load_backend_proxy_config()
    logger.info(
        "Backend proxy configuration loaded",
        extra=build_log_extra(
            "llm.proxy.config.loaded",
            backend_base_url=resolved_config.base_url,
            timeout_seconds=resolved_config.timeout_seconds,
        ),
    )


class BackendProxyClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _build_headers(
        self,
        *,
        request_id: str | None = None,
        caller_service: str | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if request_id:
            headers[REQUEST_ID_HEADER] = request_id
        if caller_service:
            headers[CALLER_SERVICE_HEADER] = caller_service
        return headers

    def get_health(self, *, request_id: str | None = None) -> httpx.Response:
        try:
            return httpx.get(
                f"{self.base_url}/internal/llm/health",
                headers=self._build_headers(request_id=request_id),
                timeout=self.timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise BackendProxyError(f"backend health proxy failed: {exc}") from exc

    def chat(
        self,
        *,
        payload: dict[str, Any],
        request_id: str | None = None,
        caller_service: str | None = None,
    ) -> httpx.Response:
        try:
            return httpx.post(
                f"{self.base_url}/internal/llm/chat",
                json=payload,
                headers=self._build_headers(request_id=request_id, caller_service=caller_service),
                timeout=self.timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise BackendProxyError(f"backend chat proxy failed: {exc}") from exc


@lru_cache(maxsize=1)
def get_backend_proxy_client() -> BackendProxyClient:
    config = load_backend_proxy_config()
    return BackendProxyClient(base_url=config.base_url, timeout_seconds=config.timeout_seconds)
