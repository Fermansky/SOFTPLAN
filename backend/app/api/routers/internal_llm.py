"""Internal LLM compatibility routes hosted inside backend."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ...core.logging import get_request_id
from ...services import (
    LlmChatPersistenceError,
    LlmImageUrlInputPart as ServiceLlmImageUrlInputPart,
    LlmServiceClient,
    LlmTextInputPart as ServiceLlmTextInputPart,
)
from ..dependencies import get_llm_service_client
from .llm import LlmChatRead, LlmUsageRead

router = APIRouter(prefix="/internal/llm", tags=["llm"])
CALLER_SERVICE_HEADER = "X-Caller-Service"


class InternalLlmHealthRead(BaseModel):
    status: Literal["ok"]


class LlmChatTextInputPart(BaseModel):
    type: Literal["text"]
    text: str = Field(min_length=1)


class LlmChatImageUrlValue(BaseModel):
    url: str = Field(min_length=1)


class LlmChatImageUrlInputPart(BaseModel):
    type: Literal["image_url"]
    image_url: LlmChatImageUrlValue


LlmChatInputPart = Annotated[LlmChatTextInputPart | LlmChatImageUrlInputPart, Field(discriminator="type")]


class InternalLlmChatRequest(BaseModel):
    prompt: str = Field(min_length=1)
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    request_id: str | None = None
    input_parts: list[LlmChatInputPart] | None = None


def _normalize_optional_header(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _to_service_input_part(part: LlmChatInputPart):
    if isinstance(part, LlmChatTextInputPart):
        return ServiceLlmTextInputPart(text=part.text)
    if isinstance(part, LlmChatImageUrlInputPart):
        return ServiceLlmImageUrlInputPart(url=part.image_url.url)
    raise TypeError(f"Unsupported llm input part: {part!r}")


@router.get("/health", response_model=InternalLlmHealthRead)
def get_internal_llm_health() -> InternalLlmHealthRead:
    return InternalLlmHealthRead(status="ok")


@router.post("/chat", response_model=LlmChatRead)
def chat(
    payload: InternalLlmChatRequest,
    request: Request,
    client: LlmServiceClient = Depends(get_llm_service_client),
) -> LlmChatRead:
    prompt = payload.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="prompt is required")

    input_parts = payload.input_parts or []
    service_input_parts = [_to_service_input_part(part) for part in input_parts] if input_parts else None
    resolved_request_id = payload.request_id or get_request_id()
    caller_service = _normalize_optional_header(request.headers.get(CALLER_SERVICE_HEADER))

    try:
        result, error = client.chat(
            prompt=prompt,
            system_prompt=payload.system_prompt,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            request_id=resolved_request_id,
            input_parts=service_input_parts,
            caller_service=caller_service,
        )
    except LlmChatPersistenceError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    if error is not None or result is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upstream LLM request failed: {error}",
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
