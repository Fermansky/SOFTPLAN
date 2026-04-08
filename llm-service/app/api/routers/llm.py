"""llm-service compatibility chat proxy router."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...core.logging import REQUEST_ID_HEADER, get_request_id
from ...services import BackendProxyClient, BackendProxyError, CALLER_SERVICE_HEADER
from ..dependencies import get_backend_proxy_client

router = APIRouter(prefix="/internal/llm", tags=["llm"])


class LlmChatTextInputPart(BaseModel):
    type: Literal["text"]
    text: str = Field(min_length=1)


class LlmChatImageUrlValue(BaseModel):
    url: str = Field(min_length=1)


class LlmChatImageUrlInputPart(BaseModel):
    type: Literal["image_url"]
    image_url: LlmChatImageUrlValue


LlmChatInputPart = Annotated[LlmChatTextInputPart | LlmChatImageUrlInputPart, Field(discriminator="type")]


class LlmChatRequest(BaseModel):
    prompt: str = Field(min_length=1)
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    request_id: str | None = None
    input_parts: list[LlmChatInputPart] | None = None


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
    request: Request,
    client: BackendProxyClient = Depends(get_backend_proxy_client),
):
    request_id = payload.request_id or request.headers.get(REQUEST_ID_HEADER) or get_request_id()
    caller_service = request.headers.get(CALLER_SERVICE_HEADER)
    try:
        response = client.chat(
            payload=payload.model_dump(exclude_none=True),
            request_id=request_id,
            caller_service=caller_service,
        )
    except BackendProxyError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    headers: dict[str, str] = {}
    proxied_request_id = response.headers.get(REQUEST_ID_HEADER) or request_id
    if proxied_request_id:
        headers[REQUEST_ID_HEADER] = proxied_request_id
    return JSONResponse(status_code=response.status_code, content=response.json(), headers=headers)
