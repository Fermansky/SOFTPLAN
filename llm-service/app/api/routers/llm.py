"""llm-service 聊天路由。

职责：
1. 校验 `/internal/llm/chat` 请求体。
2. 将 API 层输入块转换为 service 层输入块。
3. 记录请求元数据日志，并将上游失败统一映射为 502。

说明：
- 本模块只负责 HTTP 协议层，不承载上游调用细节。
- 日志仅记录 request_id、输入规模和模型信息，不记录 prompt 正文或图片内容。
"""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..dependencies import get_openai_compatible_llm_client
from ...services import (
    LlmImageUrlInputPart as ServiceLlmImageUrlInputPart,
    LlmTextInputPart as ServiceLlmTextInputPart,
    OpenAICompatibleLlmClient,
    OpenAICompatibleLlmClientError,
)

router = APIRouter(prefix="/internal/llm", tags=["llm"])
logger = logging.getLogger(__name__)


class LlmChatTextInputPart(BaseModel):
    """文本输入块。"""

    type: Literal["text"]
    text: str = Field(min_length=1)


class LlmChatImageUrlValue(BaseModel):
    """图片 URL 值对象。"""

    url: str = Field(min_length=1)


class LlmChatImageUrlInputPart(BaseModel):
    """图片 URL 输入块。"""

    type: Literal["image_url"]
    image_url: LlmChatImageUrlValue


LlmChatInputPart = Annotated[LlmChatTextInputPart | LlmChatImageUrlInputPart, Field(discriminator="type")]


class LlmChatRequest(BaseModel):
    """聊天请求。"""

    prompt: str = Field(min_length=1)
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    request_id: str | None = None
    input_parts: list[LlmChatInputPart] | None = None


class LlmUsageRead(BaseModel):
    """token 用量响应。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LlmChatRead(BaseModel):
    """聊天响应。"""

    text: str
    model: str
    usage: LlmUsageRead
    request_id: str | None = None



def _to_service_input_part(part: LlmChatInputPart):
    """将路由层输入块转换为 service 层输入块。"""
    if isinstance(part, LlmChatTextInputPart):
        return ServiceLlmTextInputPart(text=part.text)
    if isinstance(part, LlmChatImageUrlInputPart):
        return ServiceLlmImageUrlInputPart(url=part.image_url.url)
    raise TypeError(f"Unsupported llm input part: {part!r}")


@router.post("/chat", response_model=LlmChatRead)
def chat(
    payload: LlmChatRequest,
    client: OpenAICompatibleLlmClient = Depends(get_openai_compatible_llm_client),
) -> LlmChatRead:
    """处理内部聊天请求。

    流程：
    1. 规范化并校验 prompt。
    2. 统计输入块元数据并记录请求日志。
    3. 调用上游客户端。
    4. 失败统一映射为 502。
    """
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="prompt is required")

    input_parts = payload.input_parts or []
    image_part_count = sum(1 for part in input_parts if isinstance(part, LlmChatImageUrlInputPart))
    logger.info(
        "Handling llm chat request request_id=%s prompt_length=%s has_custom_model=%s input_part_count=%s image_part_count=%s",
        payload.request_id,
        len(prompt),
        bool(payload.model),
        len(input_parts),
        image_part_count,
    )

    try:
        result = client.chat(
            prompt=prompt,
            system_prompt=payload.system_prompt,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            request_id=payload.request_id,
            input_parts=[_to_service_input_part(part) for part in input_parts] if input_parts else None,
        )
    except OpenAICompatibleLlmClientError as exc:
        logger.warning(
            "LLM chat request failed request_id=%s prompt_length=%s has_custom_model=%s input_part_count=%s image_part_count=%s error=%s",
            payload.request_id,
            len(prompt),
            bool(payload.model),
            len(input_parts),
            image_part_count,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream LLM request failed: {exc}",
        ) from exc

    logger.info(
        "LLM chat request succeeded request_id=%s response_model=%s total_tokens=%s input_part_count=%s image_part_count=%s",
        result.request_id,
        result.model,
        result.usage.total_tokens,
        len(input_parts),
        image_part_count,
    )

    return LlmChatRead(
        text=result.text,
        model=result.model,
        usage=LlmUsageRead(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
        ),
        request_id=result.request_id,
    )
