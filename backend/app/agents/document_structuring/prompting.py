"""Prompt loading helpers for the document structuring agent."""

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)
_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "document_structuring_agent.txt"


class DocumentStructuringPromptError(RuntimeError):
    """Raised when the document structuring prompt is unavailable."""


def resolve_document_structuring_prompt_path() -> Path:
    configured_path = os.getenv("DOCUMENT_STRUCTURING_AGENT_PROMPT_PATH")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return _DEFAULT_PROMPT_PATH


@lru_cache(maxsize=1)
def load_document_structuring_prompt() -> str:
    prompt_path = resolve_document_structuring_prompt_path()
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        logger.warning("Document structuring prompt file is missing path=%s", prompt_path)
        raise DocumentStructuringPromptError(f"Prompt file not found: {prompt_path}") from exc
    except OSError as exc:
        logger.warning("Failed to read document structuring prompt path=%s error=%s", prompt_path, exc)
        raise DocumentStructuringPromptError(f"Failed to read prompt file: {prompt_path}") from exc

    if not prompt:
        logger.warning("Document structuring prompt file is empty path=%s", prompt_path)
        raise DocumentStructuringPromptError(f"Prompt file is empty: {prompt_path}")

    logger.info("Loaded document structuring prompt path=%s", prompt_path)
    return prompt


def get_document_structuring_prompt_snapshot() -> tuple[str, str | None]:
    prompt_path = resolve_document_structuring_prompt_path()
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except OSError:
        return str(prompt_path), None
    if not prompt:
        return str(prompt_path), None
    return str(prompt_path), hashlib.sha256(prompt.encode("utf-8")).hexdigest()

