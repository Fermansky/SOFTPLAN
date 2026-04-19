"""Helpers for extracting JSON payloads from LLM text output."""

from __future__ import annotations

import json
import re
from typing import Any, TypeAlias

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | dict[str, "JsonValue"] | list["JsonValue"]

_JSON_FENCE_PATTERN = re.compile(r"```(?P<info>[^\n`]*)\n(?P<body>.*?)```", re.DOTALL)


class LlmJsonParseError(ValueError):
    """Raised when no valid JSON payload can be extracted from model output."""


def _load_json(candidate: str) -> JsonValue:
    return json.loads(candidate)


def _iter_fenced_code_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in _JSON_FENCE_PATTERN.finditer(text):
        info = match.group("info").strip().lower()
        body = match.group("body").strip()
        if body:
            blocks.append((info, body))
    return blocks


def _looks_like_json_container(candidate: str) -> bool:
    stripped = candidate.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def _iter_balanced_json_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    text_length = len(text)

    for start_index, opening_char in enumerate(text):
        if opening_char not in "{[":
            continue

        stack = ["}" if opening_char == "{" else "]"]
        in_string = False
        escape_next = False

        for current_index in range(start_index + 1, text_length):
            current_char = text[current_index]

            if in_string:
                if escape_next:
                    escape_next = False
                    continue
                if current_char == "\\":
                    escape_next = True
                    continue
                if current_char == '"':
                    in_string = False
                continue

            if current_char == '"':
                in_string = True
                continue

            if current_char in "{[":
                stack.append("}" if current_char == "{" else "]")
                continue

            if current_char in "}]":
                if current_char != stack[-1]:
                    break
                stack.pop()
                if not stack:
                    candidates.append(text[start_index : current_index + 1].strip())
                    break

    return candidates


def parse(text: str) -> JsonValue:
    """Parse a JSON payload from raw model output."""

    normalized = text.strip()
    if not normalized:
        raise LlmJsonParseError("No JSON content found in empty text")

    try:
        return _load_json(normalized)
    except json.JSONDecodeError:
        pass

    fenced_blocks = _iter_fenced_code_blocks(text)
    for info, body in fenced_blocks:
        if info == "json":
            try:
                return _load_json(body)
            except json.JSONDecodeError:
                continue

    for _, body in fenced_blocks:
        if not _looks_like_json_container(body):
            continue
        try:
            return _load_json(body)
        except json.JSONDecodeError:
            continue

    for candidate in _iter_balanced_json_candidates(text):
        try:
            return _load_json(candidate)
        except json.JSONDecodeError:
            continue

    raise LlmJsonParseError("No valid JSON payload found in text")


def parse_object(text: str) -> dict[str, Any]:
    """Parse a top-level JSON object from raw model output."""

    parsed = parse(text)
    if not isinstance(parsed, dict):
        raise LlmJsonParseError(f"Expected top-level JSON object, got {type(parsed).__name__}")
    return parsed


def parse_array(text: str) -> list[Any]:
    """Parse a top-level JSON array from raw model output."""

    parsed = parse(text)
    if not isinstance(parsed, list):
        raise LlmJsonParseError(f"Expected top-level JSON array, got {type(parsed).__name__}")
    return parsed
