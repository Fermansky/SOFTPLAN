import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..dependencies import get_openai_compatible_llm_client
from ...services import OpenAICompatibleLlmClient, OpenAICompatibleLlmClientError

router = APIRouter(prefix="/internal/llm", tags=["llm"])
logger = logging.getLogger(__name__)


class LlmChatRequest(BaseModel):
    prompt: str = Field(min_length=1)
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    request_id: str | None = None


class LlmUsageRead(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LlmChatRead(BaseModel):
    text: str
    model: str
    usage: LlmUsageRead
    request_id: str | None = None


@router.post("/chat", response_model=LlmChatRead)
def chat(
    payload: LlmChatRequest,
    client: OpenAICompatibleLlmClient = Depends(get_openai_compatible_llm_client),
) -> LlmChatRead:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="prompt is required")

    logger.info(
        "Handling llm chat request request_id=%s prompt_length=%s has_custom_model=%s",
        payload.request_id,
        len(prompt),
        bool(payload.model),
    )

    try:
        result = client.chat(
            prompt=prompt,
            system_prompt=payload.system_prompt,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            request_id=payload.request_id,
        )
    except OpenAICompatibleLlmClientError as exc:
        logger.warning(
            "LLM chat request failed request_id=%s prompt_length=%s has_custom_model=%s error=%s",
            payload.request_id,
            len(prompt),
            bool(payload.model),
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream LLM request failed: {exc}",
        ) from exc

    logger.info(
        "LLM chat request succeeded request_id=%s response_model=%s total_tokens=%s",
        result.request_id,
        result.model,
        result.usage.total_tokens,
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
