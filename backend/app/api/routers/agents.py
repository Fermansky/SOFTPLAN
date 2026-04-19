"""Temporary debug routes for agents."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from ...agents.document_structuring import (
    DocumentStructuringAgentError,
    DocumentStructuringPromptError,
    run_document_structuring_agent,
)
from ...database import get_session
from ...services import (
    LlmChatPersistenceError,
    LlmConfigConflictError,
    LlmConfigDisabledError,
    LlmConfigNotFoundError,
    LlmConfigResolutionError,
    LlmConfigValidationError,
)

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentLlmUsageRead(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class DocumentStructuringDebugRunRequest(BaseModel):
    source_text: str = Field(min_length=1, max_length=200000)
    config_id: UUID | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    request_id: str | None = None


class DocumentStructuringDebugRunRead(BaseModel):
    output_markdown: str
    model: str
    request_id: str | None = None
    usage: AgentLlmUsageRead
    effective_config_id: UUID | None = None
    effective_config_code: str | None = None
    prompt_path: str
    prompt_hash: str | None = None


def _raise_llm_config_http_error(exc: Exception) -> None:
    if isinstance(exc, LlmConfigNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigDisabledError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigResolutionError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigValidationError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/document-structuring/debug-run", response_model=DocumentStructuringDebugRunRead)
def debug_run_document_structuring_agent(
    payload: DocumentStructuringDebugRunRequest,
    session: Session = Depends(get_session),
) -> DocumentStructuringDebugRunRead:
    try:
        result = run_document_structuring_agent(
            source_text=payload.source_text,
            session=session,
            config_id=payload.config_id,
            model=payload.model,
            temperature=payload.temperature,
            max_tokens=payload.max_tokens,
            request_id=payload.request_id,
        )
    except Exception as exc:
        _raise_llm_config_http_error(exc)
        if isinstance(exc, (DocumentStructuringPromptError, LlmChatPersistenceError)):
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
        if isinstance(exc, DocumentStructuringAgentError):
            detail = str(exc)
            if "source_text" in detail:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail) from exc
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc
        raise

    return DocumentStructuringDebugRunRead(
        output_markdown=result.output_markdown,
        model=result.model,
        request_id=result.request_id,
        usage=AgentLlmUsageRead(
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            total_tokens=result.usage.total_tokens,
        ),
        effective_config_id=result.effective_config_id,
        effective_config_code=result.effective_config_code,
        prompt_path=result.prompt_path,
        prompt_hash=result.prompt_hash,
    )
