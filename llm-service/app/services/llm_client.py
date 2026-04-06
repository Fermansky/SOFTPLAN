"""OpenAI-compatible 上游 LLM 客户端。

职责：
1. 从环境变量加载上游 LLM 配置并在启动时输出脱敏日志。
2. 将 llm-service 内部请求转换为 OpenAI-compatible chat completions 请求。
3. 对上游超时、HTTP 错误、响应结构异常做统一封装，便于路由层映射为 502。

说明：
- 本模块只负责协议适配和失败语义收敛，不承担业务提示词编排。
- 所有对外异常统一收敛为 `OpenAICompatibleLlmClientError`。
"""

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
    """上游返回的 token 用量统计。"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class LlmChatResult:
    """标准化后的聊天结果。"""

    text: str
    model: str
    usage: LlmUsage
    request_id: str | None


@dataclass(frozen=True)
class LlmTextInputPart:
    """文本输入块。"""

    text: str


@dataclass(frozen=True)
class LlmImageUrlInputPart:
    """图片 URL 输入块。"""

    url: str


LlmInputPart = LlmTextInputPart | LlmImageUrlInputPart


@dataclass(frozen=True)
class OpenAICompatibleLlmConfig:
    """上游 LLM 配置快照。"""

    base_url: str
    api_key: str
    default_model: str
    timeout_seconds: float


class OpenAICompatibleLlmClientError(RuntimeError):
    """上游 LLM 调用失败或返回结构不符合约定。"""



def _truncate_for_log(value: str, *, limit: int = _MAX_LOG_BODY_LENGTH) -> str:
    """截断日志中的大字段，避免响应体过长污染日志。"""
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...(truncated)"



def load_openai_compatible_llm_config() -> OpenAICompatibleLlmConfig:
    """从环境变量读取上游 LLM 配置。"""
    return OpenAICompatibleLlmConfig(
        base_url=os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1"),
        api_key=os.getenv("LLM_API_KEY", ""),
        default_model=os.getenv("LLM_DEFAULT_MODEL", "gpt-4o-mini"),
        timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
    )



def log_openai_compatible_llm_config(config: OpenAICompatibleLlmConfig | None = None) -> None:
    """输出脱敏后的上游配置日志。

    约束：
    - 只记录 `api_key` 是否存在，不输出真实密钥。
    """
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
    """OpenAI-compatible chat completions 客户端。"""

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
        """规范化 usage 数值字段，异常值按 0 处理。"""
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value

    def _extract_text(self, payload: dict[str, Any]) -> str:
        """从 OpenAI-compatible 响应中提取文本内容。

        兼容：
        - 纯字符串 `message.content`
        - content parts 列表中的 text 片段
        """
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
        """将内部输入块转换为上游可接受的多模态 message.content 结构。"""
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
        """调用上游 chat completions 接口。

        约束：
        - `system_prompt` 单独映射为 system message。
        - 存在 `input_parts` 时按多模态 user message 发送；否则退回纯文本 prompt。

        失败语义：
        - 缺少 API key、网络异常、HTTP 错误、JSON 结构异常时抛 `OpenAICompatibleLlmClientError`。
        """
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
            # 多模态请求统一放入单个 user message，保持与 OpenAI-compatible 接口约定一致。
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
    """按环境变量构造上游 LLM 客户端单例。"""
    config = load_openai_compatible_llm_config()
    return OpenAICompatibleLlmClient(
        base_url=config.base_url,
        api_key=config.api_key,
        default_model=config.default_model,
        timeout_seconds=config.timeout_seconds,
    )
