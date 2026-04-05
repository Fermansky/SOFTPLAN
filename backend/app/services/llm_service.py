import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx


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


class LlmServiceClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _coerce_usage_value(self, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value

    def _serialize_input_part(self, part: LlmInputPart) -> dict[str, Any]:
        if isinstance(part, LlmTextInputPart):
            return {"type": "text", "text": part.text}
        if isinstance(part, LlmImageUrlInputPart):
            return {"type": "image_url", "image_url": {"url": part.url}}
        raise TypeError(f"Unsupported llm input part: {part!r}")

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
    ) -> tuple[LlmChatResult | None, str | None]:
        url = f"{self.base_url}/internal/llm/chat"
        payload: dict[str, Any] = {"prompt": prompt}
        if system_prompt is not None:
            payload["system_prompt"] = system_prompt
        if model is not None:
            payload["model"] = model
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if request_id is not None:
            payload["request_id"] = request_id
        if input_parts is not None:
            payload["input_parts"] = [self._serialize_input_part(part) for part in input_parts]

        try:
            response = httpx.post(url, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return None, str(exc)

        if not isinstance(data, dict):
            return None, f"Unexpected llm-service payload: {data!r}"

        text = data.get("text")
        resolved_model = data.get("model")
        usage_payload = data.get("usage")
        resolved_request_id = data.get("request_id")
        if not isinstance(text, str):
            return None, f"Unexpected llm-service payload: {data!r}"
        if not isinstance(resolved_model, str):
            return None, f"Unexpected llm-service payload: {data!r}"
        if not isinstance(usage_payload, dict):
            return None, f"Unexpected llm-service payload: {data!r}"
        if resolved_request_id is not None and not isinstance(resolved_request_id, str):
            return None, f"Unexpected llm-service payload: {data!r}"

        usage = LlmUsage(
            prompt_tokens=self._coerce_usage_value(usage_payload.get("prompt_tokens")),
            completion_tokens=self._coerce_usage_value(usage_payload.get("completion_tokens")),
            total_tokens=self._coerce_usage_value(usage_payload.get("total_tokens")),
        )

        return LlmChatResult(
            text=text,
            model=resolved_model,
            usage=usage,
            request_id=resolved_request_id,
        ), None


@lru_cache(maxsize=1)
def get_llm_service_client() -> LlmServiceClient:
    base_url = os.getenv("LLM_SERVICE_BASE_URL", "http://llm-service:8000")
    timeout_seconds = float(os.getenv("LLM_SERVICE_TIMEOUT_SECONDS", "30"))
    return LlmServiceClient(base_url=base_url, timeout_seconds=timeout_seconds)

