"""Embedded LLM module hosted inside backend."""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from time import perf_counter
from typing import Any

import httpx
from sqlmodel import Session

from ..core.logging import REQUEST_ID_HEADER, build_log_extra, get_request_id
from ..database import engine
from ..models import LlmChatRecordStatus
from .llm_chat_persistence import persist_llm_chat_record

logger = logging.getLogger(__name__)
_MAX_LOG_BODY_LENGTH = 500
CALLER_SERVICE_NAME = "backend"


@dataclass(frozen=True)
class LlmUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class LlmChatResult:
    text: str
    model: str
    usage: LlmUsage
    request_id: str | None
    upstream_response_request_id: str | None = None
    upstream_response_id: str | None = None


@dataclass(frozen=True)
class LlmTextInputPart:
    text: str


@dataclass(frozen=True)
class LlmImageUrlInputPart:
    url: str


LlmInputPart = LlmTextInputPart | LlmImageUrlInputPart


@dataclass(frozen=True)
class LlmServiceConfig:
    base_url: str
    api_key: str
    default_model: str
    timeout_seconds: float


class LlmServiceExecutionError(RuntimeError):
    """Raised when the upstream LLM request fails or returns an invalid payload."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _build_duration_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _truncate_for_log(value: str, *, limit: int = _MAX_LOG_BODY_LENGTH) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...(truncated)"


def load_llm_service_config() -> LlmServiceConfig:
    return LlmServiceConfig(
        base_url=os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        api_key=os.getenv("LLM_API_KEY", ""),
        default_model=os.getenv("LLM_DEFAULT_MODEL", "gpt-4o-mini"),
        timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
    )


def log_llm_service_config(config: LlmServiceConfig | None = None) -> None:
    resolved_config = config or load_llm_service_config()
    api_key_present = bool(resolved_config.api_key.strip())
    logger.info(
        "Embedded LLM configuration loaded",
        extra=build_log_extra(
            "llm.module.config.loaded",
            base_url=resolved_config.base_url,
            default_model=resolved_config.default_model,
            timeout_seconds=resolved_config.timeout_seconds,
            api_key_present=api_key_present,
        ),
    )
    if not api_key_present:
        logger.warning(
            "LLM_API_KEY is not configured",
            extra=build_log_extra(
                "llm.module.config.missing_api_key",
                detail="/llm/chat and /internal/llm/chat will fail until a non-empty key is provided",
            ),
        )


class LlmServiceClient:
    """Backend-local LLM client with audit persistence."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds

    def _coerce_usage_value(self, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value

    def _extract_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LlmServiceExecutionError(f"Unexpected upstream payload: {payload!r}")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise LlmServiceExecutionError(f"Unexpected upstream payload: {payload!r}")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise LlmServiceExecutionError(f"Unexpected upstream payload: {payload!r}")

        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text_chunk = item.get("text")
                    if isinstance(text_chunk, str):
                        chunks.append(text_chunk)
            return "".join(chunks)
        raise LlmServiceExecutionError(f"Unexpected upstream payload: {payload!r}")

    def _serialize_input_part(self, part: LlmInputPart) -> dict[str, Any]:
        if isinstance(part, LlmTextInputPart):
            return {"type": "text", "text": part.text}
        if isinstance(part, LlmImageUrlInputPart):
            return {"type": "image_url", "image_url": {"url": part.url}}
        raise TypeError(f"Unsupported llm input part: {part!r}")

    def _execute_upstream_chat(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        request_id: str | None = None,
        input_parts: list[LlmInputPart] | None = None,
    ) -> LlmChatResult:
        target_model = model or self.default_model
        resolved_request_id = request_id or get_request_id()
        if not self.api_key.strip():
            logger.warning(
                "Embedded LLM request aborted",
                extra=build_log_extra(
                    "llm.module.request.aborted",
                    request_id=resolved_request_id,
                    base_url=self.base_url,
                    target_model=target_model,
                    reason="LLM_API_KEY is not configured",
                ),
            )
            raise LlmServiceExecutionError("LLM_API_KEY is not configured")

        messages: list[dict[str, Any]] = []
        if system_prompt is not None and system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
        if input_parts:
            messages.append(
                {
                    "role": "user",
                    "content": [self._serialize_input_part(part) for part in input_parts],
                }
            )
        else:
            messages.append({"role": "user", "content": prompt})

        request_payload: dict[str, Any] = {"model": target_model, "messages": messages}
        if temperature is not None:
            request_payload["temperature"] = temperature
        if max_tokens is not None:
            request_payload["max_tokens"] = max_tokens

        request_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if resolved_request_id:
            request_headers[REQUEST_ID_HEADER] = resolved_request_id

        url = f"{self.base_url}/chat/completions"
        try:
            response = httpx.post(
                url,
                json=request_payload,
                headers=request_headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            logger.warning(
                "Embedded LLM timeout",
                extra=build_log_extra(
                    "llm.module.timeout",
                    request_id=resolved_request_id,
                    base_url=self.base_url,
                    target_model=target_model,
                    error=str(exc),
                ),
            )
            raise LlmServiceExecutionError(f"Upstream timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            body_text = _truncate_for_log(exc.response.text.strip())
            status_code = exc.response.status_code
            logger.warning(
                "Embedded LLM HTTP error",
                extra=build_log_extra(
                    "llm.module.http_error",
                    request_id=resolved_request_id,
                    base_url=self.base_url,
                    target_model=target_model,
                    status_code=status_code,
                    response_body=body_text or "no body",
                ),
            )
            raise LlmServiceExecutionError(
                f"Upstream returned HTTP {status_code}: {body_text or 'no body'}"
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "Embedded LLM request error",
                extra=build_log_extra(
                    "llm.module.request_error",
                    request_id=resolved_request_id,
                    base_url=self.base_url,
                    target_model=target_model,
                    error_type=type(exc).__name__,
                    error=str(exc),
                ),
            )
            raise LlmServiceExecutionError(f"Upstream request error: {exc}") from exc
        except ValueError as exc:
            logger.warning(
                "Embedded LLM JSON decode error",
                extra=build_log_extra(
                    "llm.module.json_decode_error",
                    request_id=resolved_request_id,
                    base_url=self.base_url,
                    target_model=target_model,
                    error=str(exc),
                ),
            )
            raise LlmServiceExecutionError(f"Invalid upstream JSON payload: {exc}") from exc

        if not isinstance(payload, dict):
            logger.warning(
                "Embedded LLM returned unexpected top-level payload",
                extra=build_log_extra(
                    "llm.module.unexpected_payload",
                    request_id=resolved_request_id,
                    base_url=self.base_url,
                    target_model=target_model,
                    payload_type=type(payload).__name__,
                ),
            )
            raise LlmServiceExecutionError(f"Unexpected upstream payload: {payload!r}")

        try:
            text = self._extract_text(payload)
        except LlmServiceExecutionError:
            logger.warning(
                "Embedded LLM payload validation failed",
                extra=build_log_extra(
                    "llm.module.payload_validation_failed",
                    request_id=resolved_request_id,
                    base_url=self.base_url,
                    target_model=target_model,
                    payload_excerpt=_truncate_for_log(repr(payload)),
                ),
            )
            raise

        resolved_model = payload.get("model")
        if not isinstance(resolved_model, str):
            resolved_model = target_model

        usage_payload = payload.get("usage")
        if not isinstance(usage_payload, dict):
            usage_payload = {}

        usage = LlmUsage(
            prompt_tokens=self._coerce_usage_value(usage_payload.get("prompt_tokens")),
            completion_tokens=self._coerce_usage_value(usage_payload.get("completion_tokens")),
            total_tokens=self._coerce_usage_value(usage_payload.get("total_tokens")),
        )

        response_request_id = response.headers.get("x-request-id")
        payload_id = payload.get("id")
        if not isinstance(payload_id, str):
            payload_id = None
        final_request_id = resolved_request_id or response_request_id or payload_id

        return LlmChatResult(
            text=text,
            model=resolved_model,
            usage=usage,
            request_id=final_request_id,
            upstream_response_request_id=response_request_id,
            upstream_response_id=payload_id,
        )

    def check_availability(self) -> tuple[bool, str | None]:
        if not self.base_url:
            return False, "LLM_API_BASE_URL is not configured"
        if not self.api_key.strip():
            return False, "LLM_API_KEY is not configured"
        return True, None

    def chat(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        request_id: str | None = None,
        input_parts: list[LlmInputPart] | None = None,
        caller_service: str | None = CALLER_SERVICE_NAME,
    ) -> tuple[LlmChatResult | None, str | None]:
        resolved_request_id = request_id or get_request_id()
        started_at = perf_counter()
        try:
            result = self._execute_upstream_chat(
                prompt=prompt,
                system_prompt=system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                request_id=resolved_request_id,
                input_parts=input_parts,
            )
        except LlmServiceExecutionError as exc:
            completed_at = _utc_now()
            duration_ms = _build_duration_ms(started_at)
            with Session(engine) as session:
                persist_llm_chat_record(
                    session,
                    status=LlmChatRecordStatus.failed,
                    request_id=resolved_request_id,
                    caller_service=caller_service,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    input_parts=input_parts,
                    requested_model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    upstream_base_url=self.base_url,
                    completed_at=completed_at,
                    duration_ms=duration_ms,
                    error_message=str(exc),
                )
            return None, str(exc)

        completed_at = _utc_now()
        duration_ms = _build_duration_ms(started_at)
        with Session(engine) as session:
            persist_llm_chat_record(
                session,
                status=LlmChatRecordStatus.succeeded,
                request_id=resolved_request_id,
                caller_service=caller_service,
                prompt=prompt,
                system_prompt=system_prompt,
                input_parts=input_parts,
                requested_model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                upstream_base_url=self.base_url,
                completed_at=completed_at,
                duration_ms=duration_ms,
                result=result,
            )
        return result, None


@lru_cache(maxsize=1)
def get_llm_service_client() -> LlmServiceClient:
    config = load_llm_service_config()
    return LlmServiceClient(
        base_url=config.base_url,
        api_key=config.api_key,
        default_model=config.default_model,
        timeout_seconds=config.timeout_seconds,
    )
