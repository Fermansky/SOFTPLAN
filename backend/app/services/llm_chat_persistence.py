from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from sqlmodel import Session

from ..models import LlmChatRecord, LlmChatRecordStatus

if TYPE_CHECKING:
    from .llm_service import LlmChatResult, LlmInputPart

logger = logging.getLogger(__name__)


class LlmChatPersistenceError(RuntimeError):
    """Raised when llm chat audit persistence fails."""


def _infer_url_kind(url: str) -> str:
    lowered = url.lower()
    if lowered.startswith("data:"):
        return "data_url"
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        return "remote_url"
    return "other"


def _infer_content_type(url: str) -> str | None:
    if not url.lower().startswith("data:"):
        return None
    header = url[5:].split(",", 1)[0]
    mime_type = header.split(";", 1)[0].strip().lower()
    return mime_type or None


def snapshot_input_parts(input_parts: list["LlmInputPart"] | None) -> list[dict[str, object]]:
    if not input_parts:
        return []

    snapshots: list[dict[str, object]] = []
    for part in input_parts:
        text_value = getattr(part, "text", None)
        if isinstance(text_value, str):
            snapshots.append({"type": "text", "text": text_value})
            continue

        url_value = getattr(part, "url", None)
        if isinstance(url_value, str):
            snapshots.append(
                {
                    "type": "image_url",
                    "url_kind": _infer_url_kind(url_value),
                    "content_type": _infer_content_type(url_value),
                    "url_sha256": hashlib.sha256(url_value.encode("utf-8")).hexdigest(),
                }
            )
            continue

        raise TypeError(f"Unsupported llm input part: {part!r}")
    return snapshots


def persist_llm_chat_record(
    session: Session,
    *,
    status: LlmChatRecordStatus,
    request_id: str | None,
    caller_service: str | None,
    prompt: str,
    system_prompt: str | None,
    input_parts: list["LlmInputPart"] | None,
    requested_model: str | None,
    temperature: float | None,
    max_tokens: int | None,
    upstream_base_url: str,
    completed_at: datetime,
    duration_ms: int,
    result: "LlmChatResult" | None = None,
    error_message: str | None = None,
) -> LlmChatRecord:
    snapshots = snapshot_input_parts(input_parts)
    image_part_count = sum(1 for part in snapshots if part.get("type") == "image_url")
    record = LlmChatRecord(
        status=status,
        request_id=request_id,
        caller_service=caller_service,
        prompt=prompt,
        system_prompt=system_prompt,
        input_parts_snapshot=snapshots,
        input_part_count=len(snapshots),
        image_part_count=image_part_count,
        requested_model=requested_model,
        resolved_model=result.model if result is not None else None,
        temperature=temperature,
        max_tokens=max_tokens,
        prompt_tokens=result.usage.prompt_tokens if result is not None else 0,
        completion_tokens=result.usage.completion_tokens if result is not None else 0,
        total_tokens=result.usage.total_tokens if result is not None else 0,
        response_text=result.text if result is not None else None,
        error_message=error_message,
        upstream_base_url=upstream_base_url,
        upstream_response_request_id=result.upstream_response_request_id if result is not None else None,
        upstream_response_id=result.upstream_response_id if result is not None else None,
        completed_at=completed_at,
        duration_ms=duration_ms,
    )
    session.add(record)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("Failed to persist llm chat record")
        raise LlmChatPersistenceError("chat persistence failed") from exc
    session.refresh(record)
    return record

