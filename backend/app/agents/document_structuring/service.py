"""Single-run document structuring agent service."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session

from ...services import (
    LlmChatPersistenceError,
    LlmConfigError,
    LlmUsage,
    get_llm_service_client,
)
from .prompting import (
    DocumentStructuringPromptError,
    get_document_structuring_prompt_snapshot,
    load_document_structuring_prompt,
)

_CALLER_SERVICE_NAME = "backend.agent.document_structuring"
_MAX_SOURCE_TEXT_LENGTH = 200000
_DEFAULT_TEMPERATURE = 0.1


@dataclass(frozen=True)
class DocumentStructuringAgentResult:
    output_markdown: str
    model: str
    request_id: str | None
    usage: LlmUsage
    effective_config_id: UUID | None
    effective_config_code: str | None
    prompt_path: str
    prompt_hash: str | None


class DocumentStructuringAgentError(RuntimeError):
    """Raised when the document structuring agent cannot complete."""


def _normalize_source_text(source_text: str) -> str:
    normalized = source_text.strip()
    if not normalized:
        raise DocumentStructuringAgentError("source_text is required")
    if len(normalized) > _MAX_SOURCE_TEXT_LENGTH:
        raise DocumentStructuringAgentError(
            f"source_text exceeds the {_MAX_SOURCE_TEXT_LENGTH} character limit"
        )
    return normalized


def _normalize_optional_model(model: str | None) -> str | None:
    if model is None:
        return None
    normalized = model.strip()
    return normalized or None


def build_document_structuring_user_prompt(source_text: str) -> str:
    return (
        "请整理以下原始文档文本，并仅输出最终 Markdown 结果。\n"
        "原始文本开始\n"
        "<<<SOURCE_TEXT>>>\n"
        f"{source_text}\n"
        "<<<END_SOURCE_TEXT>>>\n"
        "原始文本结束"
    )


def run_document_structuring_agent(
    *,
    source_text: str,
    session: Session,
    config_id: UUID | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    request_id: str | None = None,
) -> DocumentStructuringAgentResult:
    normalized_source_text = _normalize_source_text(source_text)
    system_prompt = load_document_structuring_prompt()
    prompt_path, prompt_hash = get_document_structuring_prompt_snapshot()
    client = get_llm_service_client(config_id=config_id, session=session)

    try:
        result, error = client.chat(
            prompt=build_document_structuring_user_prompt(normalized_source_text),
            system_prompt=system_prompt,
            model=_normalize_optional_model(model),
            temperature=_DEFAULT_TEMPERATURE if temperature is None else temperature,
            max_tokens=max_tokens,
            request_id=request_id,
            caller_service=_CALLER_SERVICE_NAME,
        )
    except (LlmChatPersistenceError, LlmConfigError, DocumentStructuringPromptError):
        raise

    if error is not None or result is None:
        raise DocumentStructuringAgentError(error or "document structuring agent failed")

    return DocumentStructuringAgentResult(
        output_markdown=result.text,
        model=result.model,
        request_id=result.request_id,
        usage=result.usage,
        effective_config_id=client.config_id,
        effective_config_code=client.config_code,
        prompt_path=prompt_path,
        prompt_hash=prompt_hash,
    )
