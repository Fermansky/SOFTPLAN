import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

logger = logging.getLogger(__name__)
_MAX_LOG_BODY_LENGTH = 500


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


@dataclass(frozen=True)
class LlmTextInputPart:
    text: str


@dataclass(frozen=True)
class LlmImageUrlInputPart:
    url: str


LlmInputPart = LlmTextInputPart | LlmImageUrlInputPart


@dataclass(frozen=True)
class OpenAICompatibleLlmConfig:
    base_url: str
    api_key: str
    default_model: str
    timeout_seconds: float


class OpenAICompatibleLlmClientError(RuntimeError):
    pass


def _truncate_for_log(value: str, *, limit: int = _MAX_LOG_BODY_LENGTH) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...(truncated)"


def load_openai_compatible_llm_config() -> OpenAICompatibleLlmConfig:
    return OpenAICompatibleLlmConfig(
        base_url=os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.getenv("LLM_API_KEY", ""),
        default_model=os.getenv("LLM_DEFAULT_MODEL", "gpt-4o-mini"),
        timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
    )


def log_openai_compatible_llm_config(config: OpenAICompatibleLlmConfig | None = None) -> None:
    resolved_config = config or load_openai_compatible_llm_config()
    api_key_present = bool(resolved_config.api_key.strip())
    logger.info(
        "LLM upstream configuration loaded base_url=%s default_model=%s timeout_seconds=%s api_key_present=%s",
        resolved_config.base_url,
        resolved_config.default_model,
        resolved_config.timeout_seconds,
        api_key_present,
    )
    if not api_key_present:
        logger.warning(
            "LLM_API_KEY is not configured; /internal/llm/chat requests will fail until a non-empty key is provided"
        )


class OpenAICompatibleLlmClient:
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
            raise OpenAICompatibleLlmClientError(f"Unexpected upstream payload: {payload!r}")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise OpenAICompatibleLlmClientError(f"Unexpected upstream payload: {payload!r}")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise OpenAICompatibleLlmClientError(f"Unexpected upstream payload: {payload!r}")

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
        raise OpenAICompatibleLlmClientError(f"Unexpected upstream payload: {payload!r}")

    def _serialize_input_part(self, part: LlmInputPart) -> dict[str, Any]:
        if isinstance(part, LlmTextInputPart):
            return {"type": "text", "text": part.text}
        if isinstance(part, LlmImageUrlInputPart):
            return {"type": "image_url", "image_url": {"url": part.url}}
        raise TypeError(f"Unsupported llm input part: {part!r}")

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
    ) -> LlmChatResult:
        target_model = model or self.default_model
        if not self.api_key.strip():
            logger.warning(
                "LLM upstream request aborted because LLM_API_KEY is not configured request_id=%s base_url=%s target_model=%s",
                request_id,
                self.base_url,
                target_model,
            )
            raise OpenAICompatibleLlmClientError("LLM_API_KEY is not configured")

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

        request_payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
        }
        if temperature is not None:
            request_payload["temperature"] = temperature
        if max_tokens is not None:
            request_payload["max_tokens"] = max_tokens

        request_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if request_id:
            request_headers["X-Request-Id"] = request_id

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
                "LLM upstream timeout request_id=%s base_url=%s target_model=%s error=%s",
                request_id,
                self.base_url,
                target_model,
                exc,
            )
            raise OpenAICompatibleLlmClientError(f"Upstream timeout: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            body_text = _truncate_for_log(exc.response.text.strip())
            status_code = exc.response.status_code
            logger.warning(
                "LLM upstream HTTP error request_id=%s base_url=%s target_model=%s status_code=%s response_body=%s",
                request_id,
                self.base_url,
                target_model,
                status_code,
                body_text or "no body",
            )
            raise OpenAICompatibleLlmClientError(
                f"Upstream returned HTTP {status_code}: {body_text or 'no body'}"
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "LLM upstream request error request_id=%s base_url=%s target_model=%s error_type=%s error=%s",
                request_id,
                self.base_url,
                target_model,
                type(exc).__name__,
                exc,
            )
            raise OpenAICompatibleLlmClientError(f"Upstream request error: {exc}") from exc
        except ValueError as exc:
            logger.warning(
                "LLM upstream JSON decode error request_id=%s base_url=%s target_model=%s error=%s",
                request_id,
                self.base_url,
                target_model,
                exc,
            )
            raise OpenAICompatibleLlmClientError(f"Invalid upstream JSON payload: {exc}") from exc

        if not isinstance(payload, dict):
            logger.warning(
                "LLM upstream returned unexpected top-level payload request_id=%s base_url=%s target_model=%s payload_type=%s",
                request_id,
                self.base_url,
                target_model,
                type(payload).__name__,
            )
            raise OpenAICompatibleLlmClientError(f"Unexpected upstream payload: {payload!r}")

        try:
            text = self._extract_text(payload)
        except OpenAICompatibleLlmClientError:
            logger.warning(
                "LLM upstream payload validation failed request_id=%s base_url=%s target_model=%s payload_excerpt=%s",
                request_id,
                self.base_url,
                target_model,
                _truncate_for_log(repr(payload)),
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
        resolved_request_id = request_id or response_request_id or payload_id

        return LlmChatResult(
            text=text,
            model=resolved_model,
            usage=usage,
            request_id=resolved_request_id,
        )


@lru_cache(maxsize=1)
def get_openai_compatible_llm_client() -> OpenAICompatibleLlmClient:
    config = load_openai_compatible_llm_config()
    return OpenAICompatibleLlmClient(
        base_url=config.base_url,
        api_key=config.api_key,
        default_model=config.default_model,
        timeout_seconds=config.timeout_seconds,
    )

