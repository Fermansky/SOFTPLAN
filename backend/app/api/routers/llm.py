import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..dependencies import get_llm_service_client
from ...services import LlmServiceClient

router = APIRouter(prefix="/llm", tags=["llm"])
logger = logging.getLogger(__name__)


class LlmAvailabilityRead(BaseModel):
    available: bool
    service: str
    health_path: str | None = None
    error: str | None = None


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


@router.get("/availability", response_model=LlmAvailabilityRead)
def get_llm_availability(client: LlmServiceClient = Depends(get_llm_service_client)) -> LlmAvailabilityRead:
    logger.info("Checking llm-service availability")
    available, error = client.check_availability()
    if available:
        return LlmAvailabilityRead(
            available=True,
            service="llm-service",
            health_path="/health",
        )

    logger.warning("llm-service is unavailable: %s", error)
    return LlmAvailabilityRead(
        available=False,
        service="llm-service",
        error=error,
    )


@router.post("/chat", response_model=LlmChatRead)
def chat(
    payload: LlmChatRequest,
    client: LlmServiceClient = Depends(get_llm_service_client),
) -> LlmChatRead:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="prompt is required")

    result, error = client.chat(
        prompt=prompt,
        system_prompt=payload.system_prompt,
        model=payload.model,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        request_id=payload.request_id,
    )
    if error is not None or result is None:
        logger.warning("llm-service chat failed, error=%s", error)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"llm-service chat failed: {error}",
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
