"""Single-run text summary agent service."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlmodel import Session

from ...services import (
    LlmChatPersistenceError,
    LlmConfigError,
    LlmJsonParseError,
    LlmUsage,
    get_llm_service_client,
    parse_object,
)
from .prompting import (
    TextSummaryPromptError,
    get_text_summary_prompt_snapshot,
    load_text_summary_prompt,
)

_CALLER_SERVICE_NAME = "backend.agent.text_summary"
_MAX_SOURCE_TEXT_LENGTH = 200000
_DEFAULT_TEMPERATURE = 0.1
_MAX_TITLE_LENGTH = 60
_MAX_SUMMARY_LENGTH = 200


@dataclass(frozen=True)
class TextSummaryAgentResult:
    title: str
    summary: str
    model: str
    request_id: str | None
    usage: LlmUsage
    effective_config_id: UUID | None
    effective_config_code: str | None
    prompt_path: str
    prompt_hash: str | None


class TextSummaryAgentError(RuntimeError):
    """Raised when the text summary agent cannot complete."""


def _normalize_source_text(source_text: str) -> str:
    normalized = source_text.strip()
    if not normalized:
        raise TextSummaryAgentError("source_text is required")
    if len(normalized) > _MAX_SOURCE_TEXT_LENGTH:
        raise TextSummaryAgentError(f"source_text exceeds the {_MAX_SOURCE_TEXT_LENGTH} character limit")
    return normalized


def _normalize_optional_model(model: str | None) -> str | None:
    if model is None:
        return None
    normalized = model.strip()
    return normalized or None


def _validate_text_field(payload: dict[str, object], field_name: str, *, max_length: int) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise TextSummaryAgentError(f"text summary agent returned invalid field: {field_name}")
    normalized = value.strip()
    if not normalized:
        raise TextSummaryAgentError(f"text summary agent returned empty field: {field_name}")
    if len(normalized) > max_length:
        raise TextSummaryAgentError(f"text summary agent returned field exceeding limit: {field_name}>{max_length}")
    return normalized


def build_text_summary_user_prompt(source_text: str) -> str:
    return (
        "Please read the source text below and return JSON only.\n"
        "SOURCE TEXT BEGIN\n"
        "<<<SOURCE_TEXT>>>\n"
        f"{source_text}\n"
        "<<<END_SOURCE_TEXT>>>\n"
        "SOURCE TEXT END"
    )


def run_text_summary_agent(
    *,
    source_text: str,
    session: Session,
    config_id: UUID | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    request_id: str | None = None,
) -> TextSummaryAgentResult:
    normalized_source_text = _normalize_source_text(source_text)
    system_prompt = load_text_summary_prompt()
    prompt_path, prompt_hash = get_text_summary_prompt_snapshot()
    client = get_llm_service_client(config_id=config_id, session=session)

    try:
        result, error = client.chat(
            prompt=build_text_summary_user_prompt(normalized_source_text),
            system_prompt=system_prompt,
            model=_normalize_optional_model(model),
            temperature=_DEFAULT_TEMPERATURE if temperature is None else temperature,
            max_tokens=max_tokens,
            request_id=request_id,
            caller_service=_CALLER_SERVICE_NAME,
        )
    except (LlmChatPersistenceError, LlmConfigError, TextSummaryPromptError):
        raise

    if error is not None or result is None:
        raise TextSummaryAgentError(error or "text summary agent failed")

    try:
        parsed = parse_object(result.text)
    except LlmJsonParseError as exc:
        raise TextSummaryAgentError(f"text summary agent returned invalid json: {exc}") from exc

    title = _validate_text_field(parsed, "title", max_length=_MAX_TITLE_LENGTH)
    summary = _validate_text_field(parsed, "summary", max_length=_MAX_SUMMARY_LENGTH)

    return TextSummaryAgentResult(
        title=title,
        summary=summary,
        model=result.model,
        request_id=result.request_id,
        usage=result.usage,
        effective_config_id=client.config_id,
        effective_config_code=client.config_code,
        prompt_path=prompt_path,
        prompt_hash=prompt_hash,
    )

