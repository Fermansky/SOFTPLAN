"""内嵌 LLM 调用服务。

职责：
1. 根据请求上下文解析当前生效的 LLM 配置，并构造 OpenAI-compatible 客户端。
2. 统一封装聊天请求、响应校验、错误映射与日志打点。
3. 提供配置探针能力，用于校验远端连通性、鉴权与模型可用性。
4. 在调用结束后写入聊天审计记录，补齐配置快照、耗时与 token 统计。

说明：
- 本模块负责“一次调用”的协议适配与审计落库，不负责配置 CRUD。
- 当调用方显式传入 `config_id` 时优先使用指定配置，否则回退到当前激活配置。
- 上游调用失败统一转为 `LlmServiceExecutionError`，由路由层或任务层决定如何继续映射。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from time import perf_counter
from typing import Any
from uuid import UUID

import httpx
from sqlmodel import Session

from ..core.logging import REQUEST_ID_HEADER, build_log_extra, get_request_id
from ..database import engine
from ..models import LlmChatRecordStatus, LlmConfig
from .llm_chat_persistence import persist_llm_chat_record
from .llm_config_service import (
    LlmConfigValidationError,
    get_llm_config_or_raise,
    resolve_llm_config,
    validate_llm_config_values,
)

logger = logging.getLogger(__name__)
_MAX_LOG_BODY_LENGTH = 500
_MAX_PROBE_TIMEOUT_SECONDS = 5.0
_MODEL_LIST_PROBE_DEFAULT_MODEL = "__models_probe__"
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
    reasoning_content: str | None = None
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
    config_id: UUID | None
    config_code: str | None
    base_url: str
    api_key: str
    default_model: str
    timeout_seconds: float


class LlmConfigValidationDepth(str, Enum):
    """LLM 配置探针深度。"""

    basic = "basic"
    strict = "strict"


@dataclass(frozen=True)
class LlmConfigValidationResult:
    """LLM 配置探针结果。"""

    valid: bool
    stage: str
    normalized_base_url: str
    model_checked: bool
    latency_ms: int | None = None
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class LlmConfigModelsResult:
    success: bool
    normalized_base_url: str
    model_ids: list[str]
    latency_ms: int | None = None
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class LlmServiceExecutionError(RuntimeError):
    """上游 LLM 请求失败或返回非法载荷时抛出。"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _build_duration_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _truncate_for_log(value: str, *, limit: int = _MAX_LOG_BODY_LENGTH) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...(truncated)"


def _probe_timeout_seconds(timeout_seconds: float) -> float:
    """返回探针请求使用的短超时，避免校验接口被慢上游拖住。"""

    if timeout_seconds <= 0:
        return _MAX_PROBE_TIMEOUT_SECONDS
    return min(timeout_seconds, _MAX_PROBE_TIMEOUT_SECONDS)


def _build_probe_headers(api_key: str, *, request_id: str | None) -> dict[str, str]:
    """构造探针请求头，统一附带鉴权与请求链路标识。"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    return headers


def _build_validation_result(
    *,
    valid: bool,
    stage: str,
    normalized_base_url: str,
    model_checked: bool,
    latency_ms: int | None = None,
    http_status: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> LlmConfigValidationResult:
    """统一创建结构化探针结果，减少不同分支的字段遗漏。"""
    return LlmConfigValidationResult(
        valid=valid,
        stage=stage,
        normalized_base_url=normalized_base_url,
        model_checked=model_checked,
        latency_ms=latency_ms,
        http_status=http_status,
        error_code=error_code,
        error_message=error_message,
    )


def _build_models_result(
    *,
    success: bool,
    normalized_base_url: str,
    model_ids: list[str],
    latency_ms: int | None = None,
    http_status: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> LlmConfigModelsResult:
    return LlmConfigModelsResult(
        success=success,
        normalized_base_url=normalized_base_url,
        model_ids=model_ids,
        latency_ms=latency_ms,
        http_status=http_status,
        error_code=error_code,
        error_message=error_message,
    )


def _extract_model_ids(payload: Any) -> list[str]:
    """从 OpenAI-compatible `/models` 响应里提取可比较的模型标识列表。"""

    items: list[Any] | None = None
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            items = data
        models = payload.get("models")
        if items is None and isinstance(models, list):
            items = models
    elif isinstance(payload, list):
        items = payload

    if items is None:
        return []

    model_ids: list[str] = []
    for item in items:
        if isinstance(item, str):
            model_ids.append(item)
            continue
        if isinstance(item, dict):
            model_id = item.get("id") or item.get("name")
            if isinstance(model_id, str) and model_id:
                model_ids.append(model_id)
    return model_ids


def _dedupe_model_ids(model_ids: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for model_id in model_ids:
        if model_id in seen:
            continue
        seen.add(model_id)
        deduped.append(model_id)
    return deduped


def _build_models_probe_client(
    *,
    config_id: UUID | None,
    config_code: str | None,
    base_url: str,
    api_key: str,
    timeout_seconds: float,
) -> "LlmServiceClient":
    validated = validate_llm_config_values(
        base_url=base_url,
        api_key=api_key,
        default_model=_MODEL_LIST_PROBE_DEFAULT_MODEL,
        timeout_seconds=timeout_seconds,
        require_api_key=True,
    )
    return LlmServiceClient(
        config_id=config_id,
        config_code=config_code,
        base_url=validated["base_url"],
        api_key=validated["api_key"],
        default_model=_MODEL_LIST_PROBE_DEFAULT_MODEL,
        timeout_seconds=validated["timeout_seconds"],
    )


def _looks_like_model_error(body_text: str) -> bool:
    """粗略判断上游错误体是否表达了“模型不存在/不可用”语义。"""

    lowered = body_text.lower()
    return any(
        needle in lowered
        for needle in (
            "model not found",
            "unknown model",
            "does not exist",
            "invalid model",
            "unsupported model",
            "no such model",
        )
    ) or ("model" in lowered and "not found" in lowered)


def build_llm_service_config_from_model(llm_config: LlmConfig) -> LlmServiceConfig:
    """把持久化配置实体转换为运行时客户端配置。"""

    return LlmServiceConfig(
        config_id=llm_config.id,
        config_code=llm_config.code,
        base_url=llm_config.base_url.rstrip("/"),
        api_key=llm_config.api_key,
        default_model=llm_config.default_model,
        timeout_seconds=llm_config.timeout_seconds,
    )


def load_llm_service_config(session: Session | None = None, *, config_id: UUID | None = None) -> LlmServiceConfig:
    """解析指定或当前激活的 LLM 配置，并转换为运行时配置对象。"""

    if session is None:
        with Session(engine) as managed_session:
            return load_llm_service_config(managed_session, config_id=config_id)

    llm_config = resolve_llm_config(session, config_id=config_id)
    return build_llm_service_config_from_model(llm_config)


def log_llm_service_config(config: LlmServiceConfig | None = None) -> None:
    """记录当前生效 LLM 配置的关键摘要，便于启动期排障。"""

    try:
        resolved_config = config or load_llm_service_config()
    except RuntimeError as exc:
        logger.warning(
            "Active llm configuration is unavailable",
            extra=build_log_extra("llm.module.config.unavailable", error=str(exc)),
        )
        return

    api_key_present = bool(resolved_config.api_key.strip())
    logger.info(
        "Embedded LLM configuration loaded",
        extra=build_log_extra(
            "llm.module.config.loaded",
            config_id=str(resolved_config.config_id) if resolved_config.config_id is not None else None,
            config_code=resolved_config.config_code,
            base_url=resolved_config.base_url,
            default_model=resolved_config.default_model,
            timeout_seconds=resolved_config.timeout_seconds,
            api_key_present=api_key_present,
        ),
    )
    if not api_key_present:
        logger.warning(
            "LLM api key is not configured",
            extra=build_log_extra(
                "llm.module.config.missing_api_key",
                config_id=str(resolved_config.config_id) if resolved_config.config_id is not None else None,
                config_code=resolved_config.config_code,
                detail="/llm/chat requests will fail until a non-empty key is provided",
            ),
        )


class LlmServiceClient:
    """面向 backend 内部调用方的 LLM 客户端。"""

    def __init__(
        self,
        *,
        config_id: UUID | None,
        config_code: str | None,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.config_id = config_id
        self.config_code = config_code
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds

    def _coerce_usage_value(self, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value

    def _normalize_message_text(self, value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            chunks: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    text_chunk = item.get("text")
                    if isinstance(text_chunk, str):
                        chunks.append(text_chunk)
            return "".join(chunks)
        return None

    def _split_reasoning_content(self, content: str) -> tuple[str, str | None]:
        if "</think>" not in content:
            return content, None
        reasoning_content, response_text = content.split("</think>", 1)
        return response_text, reasoning_content

    def _extract_text(self, payload: dict[str, Any]) -> tuple[str, str | None]:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LlmServiceExecutionError(f"Unexpected upstream payload: {payload!r}")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise LlmServiceExecutionError(f"Unexpected upstream payload: {payload!r}")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise LlmServiceExecutionError(f"Unexpected upstream payload: {payload!r}")

        content = self._normalize_message_text(message.get("content"))
        if content is None:
            raise LlmServiceExecutionError(f"Unexpected upstream payload: {payload!r}")

        reasoning_content = self._normalize_message_text(message.get("reasoning_content"))
        if reasoning_content is not None:
            return content, reasoning_content

        return self._split_reasoning_content(content)

    def _serialize_input_part(self, part: LlmInputPart) -> dict[str, Any]:
        if isinstance(part, LlmTextInputPart):
            return {"type": "text", "text": part.text}
        if isinstance(part, LlmImageUrlInputPart):
            return {"type": "image_url", "image_url": {"url": part.url}}
        raise TypeError(f"Unsupported llm input part: {part!r}")

    def _probe_models(
        self,
        *,
        request_id: str | None,
    ) -> tuple[LlmConfigValidationResult, list[str] | None, bool]:
        """调用 `/models` 探测基础连通性，并尽量提取可用模型列表。

        返回值中的布尔位表示 strict 模式下是否允许回退到最小 chat probe。
        """

        started_at = perf_counter()
        url = f"{self.base_url}/models"
        try:
            response = httpx.get(
                url,
                headers=_build_probe_headers(self.api_key, request_id=request_id),
                timeout=_probe_timeout_seconds(self.timeout_seconds),
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            return (
                _build_validation_result(
                    valid=False,
                    stage="network",
                    normalized_base_url=self.base_url,
                    model_checked=False,
                    latency_ms=_build_duration_ms(started_at),
                    error_code="timeout",
                    error_message=f"Upstream timeout: {exc}",
                ),
                None,
                False,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            body_text = _truncate_for_log(exc.response.text.strip())
            if status_code in {401, 403}:
                result = _build_validation_result(
                    valid=False,
                    stage="auth",
                    normalized_base_url=self.base_url,
                    model_checked=False,
                    latency_ms=_build_duration_ms(started_at),
                    http_status=status_code,
                    error_code="auth_failed",
                    error_message=f"Upstream rejected credentials with HTTP {status_code}",
                )
                return result, None, False
            if status_code in {404, 405, 501}:
                result = _build_validation_result(
                    valid=False,
                    stage="upstream",
                    normalized_base_url=self.base_url,
                    model_checked=False,
                    latency_ms=_build_duration_ms(started_at),
                    http_status=status_code,
                    error_code="models_endpoint_unavailable",
                    error_message=f"Upstream /models endpoint is unavailable with HTTP {status_code}",
                )
                return result, None, True
            result = _build_validation_result(
                valid=False,
                stage="upstream",
                normalized_base_url=self.base_url,
                model_checked=False,
                latency_ms=_build_duration_ms(started_at),
                http_status=status_code,
                error_code="http_error",
                error_message=f"Upstream returned HTTP {status_code}: {body_text or 'no body'}",
            )
            return result, None, False
        except httpx.RequestError as exc:
            return (
                _build_validation_result(
                    valid=False,
                    stage="network",
                    normalized_base_url=self.base_url,
                    model_checked=False,
                    latency_ms=_build_duration_ms(started_at),
                    error_code="request_error",
                    error_message=f"Upstream request error: {exc}",
                ),
                None,
                False,
            )
        except ValueError as exc:
            return (
                _build_validation_result(
                    valid=False,
                    stage="upstream",
                    normalized_base_url=self.base_url,
                    model_checked=False,
                    latency_ms=_build_duration_ms(started_at),
                    error_code="invalid_json",
                    error_message=f"Invalid upstream JSON payload: {exc}",
                ),
                None,
                False,
            )

        model_ids = _extract_model_ids(payload)
        if not model_ids:
            return (
                _build_validation_result(
                    valid=False,
                    stage="upstream",
                    normalized_base_url=self.base_url,
                    model_checked=False,
                    latency_ms=_build_duration_ms(started_at),
                    http_status=response.status_code,
                    error_code="invalid_models_payload",
                    error_message="Upstream /models response did not include model identifiers",
                ),
                None,
                True,
            )

        return (
            _build_validation_result(
                valid=True,
                stage="network",
                normalized_base_url=self.base_url,
                model_checked=False,
                latency_ms=_build_duration_ms(started_at),
                http_status=response.status_code,
            ),
            model_ids,
            False,
        )

    def _probe_chat_completion(self, *, request_id: str | None) -> LlmConfigValidationResult:
        """执行最低成本的 chat probe，确认默认模型确实可以被调用。"""

        started_at = perf_counter()
        url = f"{self.base_url}/chat/completions"
        headers = _build_probe_headers(self.api_key, request_id=request_id)
        headers["Content-Type"] = "application/json"
        request_payload = {
            "model": self.default_model,
            "messages": [{"role": "user", "content": "validation probe"}],
            "max_tokens": 1,
            "temperature": 0,
        }
        try:
            response = httpx.post(
                url,
                json=request_payload,
                headers=headers,
                timeout=_probe_timeout_seconds(self.timeout_seconds),
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            return _build_validation_result(
                valid=False,
                stage="network",
                normalized_base_url=self.base_url,
                model_checked=True,
                latency_ms=_build_duration_ms(started_at),
                error_code="timeout",
                error_message=f"Upstream timeout: {exc}",
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            body_text = _truncate_for_log(exc.response.text.strip())
            if status_code in {401, 403}:
                return _build_validation_result(
                    valid=False,
                    stage="auth",
                    normalized_base_url=self.base_url,
                    model_checked=True,
                    latency_ms=_build_duration_ms(started_at),
                    http_status=status_code,
                    error_code="auth_failed",
                    error_message=f"Upstream rejected credentials with HTTP {status_code}",
                )
            if status_code in {400, 404} and _looks_like_model_error(body_text):
                return _build_validation_result(
                    valid=False,
                    stage="model",
                    normalized_base_url=self.base_url,
                    model_checked=True,
                    latency_ms=_build_duration_ms(started_at),
                    http_status=status_code,
                    error_code="model_not_found",
                    error_message=f"Configured model '{self.default_model}' is not available upstream",
                )
            return _build_validation_result(
                valid=False,
                stage="upstream",
                normalized_base_url=self.base_url,
                model_checked=True,
                latency_ms=_build_duration_ms(started_at),
                http_status=status_code,
                error_code="http_error",
                error_message=f"Upstream returned HTTP {status_code}: {body_text or 'no body'}",
            )
        except httpx.RequestError as exc:
            return _build_validation_result(
                valid=False,
                stage="network",
                normalized_base_url=self.base_url,
                model_checked=True,
                latency_ms=_build_duration_ms(started_at),
                error_code="request_error",
                error_message=f"Upstream request error: {exc}",
            )
        except ValueError as exc:
            return _build_validation_result(
                valid=False,
                stage="upstream",
                normalized_base_url=self.base_url,
                model_checked=True,
                latency_ms=_build_duration_ms(started_at),
                error_code="invalid_json",
                error_message=f"Invalid upstream JSON payload: {exc}",
            )

        if not isinstance(payload, dict):
            return _build_validation_result(
                valid=False,
                stage="upstream",
                normalized_base_url=self.base_url,
                model_checked=True,
                latency_ms=_build_duration_ms(started_at),
                http_status=response.status_code,
                error_code="invalid_payload",
                error_message=f"Unexpected upstream payload: {payload!r}",
            )

        try:
            self._extract_text(payload)
        except LlmServiceExecutionError as exc:
            return _build_validation_result(
                valid=False,
                stage="upstream",
                normalized_base_url=self.base_url,
                model_checked=True,
                latency_ms=_build_duration_ms(started_at),
                http_status=response.status_code,
                error_code="invalid_payload",
                error_message=str(exc),
            )

        return _build_validation_result(
            valid=True,
            stage="model",
            normalized_base_url=self.base_url,
            model_checked=True,
            latency_ms=_build_duration_ms(started_at),
            http_status=response.status_code,
        )

    def probe(
        self,
        depth: LlmConfigValidationDepth = LlmConfigValidationDepth.basic,
        *,
        request_id: str | None = None,
    ) -> LlmConfigValidationResult:
        """执行 LLM 配置探针。

        `basic` 只验证静态合法性与远端可连接性；`strict` 还会确认默认模型是否可用。
        """

        normalized_base_url = self.base_url.strip().rstrip("/")
        try:
            validated = validate_llm_config_values(
                base_url=self.base_url,
                api_key=self.api_key,
                default_model=self.default_model,
                timeout_seconds=self.timeout_seconds,
                require_api_key=True,
            )
        except LlmConfigValidationError as exc:
            return _build_validation_result(
                valid=False,
                stage="static",
                normalized_base_url=normalized_base_url,
                model_checked=False,
                error_code="invalid_config",
                error_message=str(exc),
            )

        probe_client = LlmServiceClient(
            config_id=self.config_id,
            config_code=self.config_code,
            base_url=validated["base_url"],
            api_key=validated["api_key"],
            default_model=validated["default_model"],
            timeout_seconds=validated["timeout_seconds"],
        )
        models_result, model_ids, fallback_allowed = probe_client._probe_models(request_id=request_id)
        if depth == LlmConfigValidationDepth.basic:
            return models_result

        if models_result.valid:
            if probe_client.default_model in (model_ids or []):
                return _build_validation_result(
                    valid=True,
                    stage="model",
                    normalized_base_url=probe_client.base_url,
                    model_checked=True,
                    latency_ms=models_result.latency_ms,
                    http_status=models_result.http_status,
                )
            return _build_validation_result(
                valid=False,
                stage="model",
                normalized_base_url=probe_client.base_url,
                model_checked=True,
                latency_ms=models_result.latency_ms,
                http_status=models_result.http_status,
                error_code="model_not_found",
                error_message=f"Configured model '{probe_client.default_model}' is not listed by upstream",
            )

        if not fallback_allowed:
            return models_result

        return probe_client._probe_chat_completion(request_id=request_id)

    def list_models(self, *, request_id: str | None = None) -> LlmConfigModelsResult:
        normalized_base_url = self.base_url.strip().rstrip("/")
        try:
            probe_client = _build_models_probe_client(
                config_id=self.config_id,
                config_code=self.config_code,
                base_url=self.base_url,
                api_key=self.api_key,
                timeout_seconds=self.timeout_seconds,
            )
        except LlmConfigValidationError as exc:
            return _build_models_result(
                success=False,
                normalized_base_url=normalized_base_url,
                model_ids=[],
                error_code="invalid_config",
                error_message=str(exc),
            )

        models_result, model_ids, _ = probe_client._probe_models(request_id=request_id)
        return _build_models_result(
            success=models_result.valid,
            normalized_base_url=probe_client.base_url,
            model_ids=_dedupe_model_ids(model_ids or []),
            latency_ms=models_result.latency_ms,
            http_status=models_result.http_status,
            error_code=models_result.error_code,
            error_message=models_result.error_message,
        )

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
                    config_id=str(self.config_id) if self.config_id is not None else None,
                    config_code=self.config_code,
                    base_url=self.base_url,
                    target_model=target_model,
                    reason="LLM api key is not configured",
                ),
            )
            raise LlmServiceExecutionError("LLM api key is not configured")

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
                    config_id=str(self.config_id) if self.config_id is not None else None,
                    config_code=self.config_code,
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
                    config_id=str(self.config_id) if self.config_id is not None else None,
                    config_code=self.config_code,
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
                    config_id=str(self.config_id) if self.config_id is not None else None,
                    config_code=self.config_code,
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
                    config_id=str(self.config_id) if self.config_id is not None else None,
                    config_code=self.config_code,
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
                    config_id=str(self.config_id) if self.config_id is not None else None,
                    config_code=self.config_code,
                    base_url=self.base_url,
                    target_model=target_model,
                    payload_type=type(payload).__name__,
                ),
            )
            raise LlmServiceExecutionError(f"Unexpected upstream payload: {payload!r}")

        try:
            text, reasoning_content = self._extract_text(payload)
        except LlmServiceExecutionError:
            logger.warning(
                "Embedded LLM payload validation failed",
                extra=build_log_extra(
                    "llm.module.payload_validation_failed",
                    request_id=resolved_request_id,
                    config_id=str(self.config_id) if self.config_id is not None else None,
                    config_code=self.config_code,
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
            reasoning_content=reasoning_content,
            upstream_response_request_id=response_request_id,
            upstream_response_id=payload_id,
        )

    def check_availability(self) -> tuple[bool, str | None]:
        """兼容旧调用方的可用性检查接口，内部委托给 basic probe。"""

        result = self.probe(LlmConfigValidationDepth.basic)
        return result.valid, result.error_message

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
                    llm_config_id=self.config_id,
                    llm_config_code=self.config_code,
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
                llm_config_id=self.config_id,
                llm_config_code=self.config_code,
                requested_model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                upstream_base_url=self.base_url,
                completed_at=completed_at,
                duration_ms=duration_ms,
                result=result,
            )
        return result, None


def validate_llm_service_config(
    config: LlmServiceConfig,
    *,
    depth: LlmConfigValidationDepth = LlmConfigValidationDepth.basic,
    request_id: str | None = None,
) -> LlmConfigValidationResult:
    """对运行时配置对象执行探针，适合激活前复用。"""

    client = LlmServiceClient(
        config_id=config.config_id,
        config_code=config.config_code,
        base_url=config.base_url,
        api_key=config.api_key,
        default_model=config.default_model,
        timeout_seconds=config.timeout_seconds,
    )
    return client.probe(depth=depth, request_id=request_id)


def validate_llm_config_by_id(
    session: Session,
    config_id: UUID,
    *,
    depth: LlmConfigValidationDepth = LlmConfigValidationDepth.basic,
    request_id: str | None = None,
) -> LlmConfigValidationResult:
    """按配置 id 读取持久化配置并执行指定深度的探针。"""

    llm_config = get_llm_config_or_raise(session, config_id)
    return validate_llm_service_config(
        build_llm_service_config_from_model(llm_config),
        depth=depth,
        request_id=request_id,
    )


def list_llm_models_by_config_id(
    session: Session,
    config_id: UUID,
    *,
    request_id: str | None = None,
) -> LlmConfigModelsResult:
    client = get_llm_service_client(config_id=config_id, session=session)
    return client.list_models(request_id=request_id)


def preview_llm_models(
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float,
    request_id: str | None = None,
) -> LlmConfigModelsResult:
    probe_client = _build_models_probe_client(
        config_id=None,
        config_code=None,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
    return probe_client.list_models(request_id=request_id)


def get_llm_service_client(*, config_id: UUID | None = None, session: Session | None = None) -> LlmServiceClient:
    """根据指定配置或当前激活配置构造 LLM 服务客户端。"""

    config = load_llm_service_config(session=session, config_id=config_id)
    return LlmServiceClient(
        config_id=config.config_id,
        config_code=config.config_code,
        base_url=config.base_url,
        api_key=config.api_key,
        default_model=config.default_model,
        timeout_seconds=config.timeout_seconds,
    )

