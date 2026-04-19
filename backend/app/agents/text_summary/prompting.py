"""Prompt loading helpers for the text summary agent."""

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)
_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "text_summary_agent.txt"


class TextSummaryPromptError(RuntimeError):
    """Raised when the text summary prompt is unavailable."""


def resolve_text_summary_prompt_path() -> Path:
    configured_path = os.getenv("DOCUMENT_TEXT_SUMMARY_PROMPT_PATH")
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return _DEFAULT_PROMPT_PATH


@lru_cache(maxsize=1)
def load_text_summary_prompt() -> str:
    prompt_path = resolve_text_summary_prompt_path()
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        logger.warning("Text summary prompt file is missing path=%s", prompt_path)
        raise TextSummaryPromptError(f"Prompt file not found: {prompt_path}") from exc
    except OSError as exc:
        logger.warning("Failed to read text summary prompt path=%s error=%s", prompt_path, exc)
        raise TextSummaryPromptError(f"Failed to read prompt file: {prompt_path}") from exc

    if not prompt:
        logger.warning("Text summary prompt file is empty path=%s", prompt_path)
        raise TextSummaryPromptError(f"Prompt file is empty: {prompt_path}")

    logger.info("Loaded text summary prompt path=%s", prompt_path)
    return prompt


def get_text_summary_prompt_snapshot() -> tuple[str, str | None]:
    prompt_path = resolve_text_summary_prompt_path()
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except OSError:
        return str(prompt_path), None
    if not prompt:
        return str(prompt_path), None
    return str(prompt_path), hashlib.sha256(prompt.encode("utf-8")).hexdigest()

