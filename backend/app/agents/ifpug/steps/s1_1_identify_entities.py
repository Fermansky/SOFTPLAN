"""子任务 1.1：识别计数范围内所有逻辑相关、用户可识别的数据/控制信息。

输入（来自 ctx）：
- source_document：已结构化的文档文本（建议为 markdown）
- counting_scope：用户描述的计数范围
- user_requirements：用户需求描述

输出（写回 ctx）：
- candidate_entities：候选数据实体列表（带稳定 id、属性、原文引用）
- StepRecord：含模型、prompt 指纹、usage 与漏斗指标 metrics

设计要点：
- LLM **不分配 id**，只输出实体的语义字段；id 由 ctx 在代码侧统一分配，
  以便后续步骤通过稳定 id 引用实体而不会"改名漂移"。
- LLM 输出经过严格的 schema 校验，任何字段缺失/类型错误都会抛
  `IdentifyEntitiesAgentError`，由 runner 统一包装。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlmodel import Session

from ....services import (
    LlmChatPersistenceError,
    LlmConfigError,
    LlmJsonParseError,
    LlmUsage,
    get_llm_service_client,
    parse_object,
)
from ..._common import PromptLoader
from ...pipeline import StepRecord, StepStatus
from ..context import IfpugContext
from ..domain import Attribute, DataEntity, SourceRef

logger = logging.getLogger(__name__)

_CALLER_SERVICE_NAME = "backend.agent.ifpug.s1_1_identify_entities"
_DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "prompts"
    / "ifpug_s1_1_identify_entities.txt"
)
_PROMPT_ENV_VAR = "IFPUG_S1_1_IDENTIFY_ENTITIES_PROMPT_PATH"
_MAX_SOURCE_DOCUMENT_LENGTH = 200000
_MAX_CONTEXT_FIELD_LENGTH = 8000
_DEFAULT_TEMPERATURE = 0.1
_MAX_NAME_LENGTH = 200
_MAX_DESCRIPTION_LENGTH = 2000
_MAX_QUOTE_LENGTH = 1000
_MAX_LOCATION_LENGTH = 500


# ---------------------------------------------------------------------------
# 领域错误
# ---------------------------------------------------------------------------


class IdentifyEntitiesPromptError(RuntimeError):
    """读取 system prompt 文件失败时抛出。"""


class IdentifyEntitiesAgentError(RuntimeError):
    """子任务 1.1 无法完成时抛出。"""


# ---------------------------------------------------------------------------
# Prompt 加载（通过通用 PromptLoader 完成；保留旧的公开符号名作为门面）
# ---------------------------------------------------------------------------


_prompt_loader = PromptLoader(
    default_path=_DEFAULT_PROMPT_PATH,
    env_var=_PROMPT_ENV_VAR,
    error_cls=IdentifyEntitiesPromptError,
    label="ifpug s1_1",
)


def _resolve_prompt_path() -> Path:
    return _prompt_loader.resolve_path()


# 直接绑定 lru_cache 装饰的可调用对象，保留 ``.cache_clear()`` 接口给测试。
load_identify_entities_prompt = _prompt_loader.cached_loader


def get_identify_entities_prompt_snapshot() -> tuple[str, str | None]:
    return _prompt_loader.snapshot()


# ---------------------------------------------------------------------------
# Agent 结果数据
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentifyEntitiesAgentResult:
    """LLM 调用的原始返回结果（实体尚未分配 ctx 级 id）。"""

    entities: list[DataEntity]
    model: str
    request_id: str | None
    usage: LlmUsage
    effective_config_id: UUID | None
    effective_config_code: str | None
    prompt_path: str
    prompt_hash: str | None


# ---------------------------------------------------------------------------
# 入参规范化与输出校验
# ---------------------------------------------------------------------------


def _normalize_required_text(value: str, *, name: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise IdentifyEntitiesAgentError(f"{name} is required")
    if len(normalized) > max_length:
        raise IdentifyEntitiesAgentError(
            f"{name} exceeds the {max_length} character limit"
        )
    return normalized


def _normalize_optional_text(value: str | None, *, name: str, max_length: int) -> str:
    if value is None:
        return ""
    normalized = value.strip()
    if len(normalized) > max_length:
        raise IdentifyEntitiesAgentError(
            f"{name} exceeds the {max_length} character limit"
        )
    return normalized


def _normalize_optional_model(model: str | None) -> str | None:
    if model is None:
        return None
    normalized = model.strip()
    return normalized or None


def _ensure_string(payload: Any, *, field_name: str, max_length: int, allow_empty: bool = False) -> str:
    if not isinstance(payload, str):
        raise IdentifyEntitiesAgentError(
            f"identify_entities agent returned invalid field: {field_name}"
        )
    normalized = payload.strip()
    if not allow_empty and not normalized:
        raise IdentifyEntitiesAgentError(
            f"identify_entities agent returned empty field: {field_name}"
        )
    if len(normalized) > max_length:
        raise IdentifyEntitiesAgentError(
            f"identify_entities agent returned field exceeding limit: {field_name}>{max_length}"
        )
    return normalized


def _parse_attribute(raw: Any, *, index: int, entity_index: int) -> Attribute:
    if not isinstance(raw, dict):
        raise IdentifyEntitiesAgentError(
            f"identify_entities agent returned invalid attribute at entities[{entity_index}].attributes[{index}]"
        )
    name = _ensure_string(
        raw.get("name"),
        field_name=f"entities[{entity_index}].attributes[{index}].name",
        max_length=_MAX_NAME_LENGTH,
    )
    description = _ensure_string(
        raw.get("description", ""),
        field_name=f"entities[{entity_index}].attributes[{index}].description",
        max_length=_MAX_DESCRIPTION_LENGTH,
        allow_empty=True,
    )
    return Attribute(name=name, description=description or None)


def _parse_source_ref(raw: Any, *, index: int, entity_index: int) -> SourceRef:
    if not isinstance(raw, dict):
        raise IdentifyEntitiesAgentError(
            f"identify_entities agent returned invalid source_ref at entities[{entity_index}].source_refs[{index}]"
        )
    quote = _ensure_string(
        raw.get("quote"),
        field_name=f"entities[{entity_index}].source_refs[{index}].quote",
        max_length=_MAX_QUOTE_LENGTH,
    )
    location_raw = raw.get("location")
    if location_raw is None:
        location: str | None = None
    else:
        location = _ensure_string(
            location_raw,
            field_name=f"entities[{entity_index}].source_refs[{index}].location",
            max_length=_MAX_LOCATION_LENGTH,
            allow_empty=True,
        ) or None
    return SourceRef(quote=quote, location=location)


def _parse_entities_payload(payload: dict[str, Any]) -> list[DataEntity]:
    entities_raw = payload.get("entities")
    if not isinstance(entities_raw, list):
        raise IdentifyEntitiesAgentError(
            "identify_entities agent returned invalid field: entities"
        )

    seen_names: set[str] = set()
    entities: list[DataEntity] = []
    for entity_index, raw in enumerate(entities_raw):
        if not isinstance(raw, dict):
            raise IdentifyEntitiesAgentError(
                f"identify_entities agent returned invalid entity at entities[{entity_index}]"
            )
        name = _ensure_string(
            raw.get("name"),
            field_name=f"entities[{entity_index}].name",
            max_length=_MAX_NAME_LENGTH,
        )
        # 同名实体合并为一项，避免 LLM 重复输出导致下游计数膨胀。
        if name in seen_names:
            logger.info("ifpug s1_1 dropping duplicate entity name=%s", name)
            continue
        seen_names.add(name)

        description = _ensure_string(
            raw.get("description", ""),
            field_name=f"entities[{entity_index}].description",
            max_length=_MAX_DESCRIPTION_LENGTH,
            allow_empty=True,
        )

        attributes_raw = raw.get("attributes", [])
        if not isinstance(attributes_raw, list):
            raise IdentifyEntitiesAgentError(
                f"identify_entities agent returned invalid field: entities[{entity_index}].attributes"
            )
        attributes = [
            _parse_attribute(a, index=i, entity_index=entity_index)
            for i, a in enumerate(attributes_raw)
        ]

        source_refs_raw = raw.get("source_refs", [])
        if not isinstance(source_refs_raw, list):
            raise IdentifyEntitiesAgentError(
                f"identify_entities agent returned invalid field: entities[{entity_index}].source_refs"
            )
        source_refs = [
            _parse_source_ref(s, index=i, entity_index=entity_index)
            for i, s in enumerate(source_refs_raw)
        ]

        entities.append(
            DataEntity(
                id="",  # 由调用方分配
                name=name,
                description=description,
                attributes=attributes,
                source_refs=source_refs,
            )
        )

    return entities


# ---------------------------------------------------------------------------
# User Prompt 构建与 Agent 主入口
# ---------------------------------------------------------------------------


def build_identify_entities_user_prompt(
    *,
    source_document: str,
    counting_scope: str,
    user_requirements: str,
) -> str:
    return (
        "请阅读以下输入并仅返回 JSON。\n"
        "计数范围：\n"
        "<<<COUNTING_SCOPE>>>\n"
        f"{counting_scope or '（未提供）'}\n"
        "<<<END_COUNTING_SCOPE>>>\n"
        "用户需求：\n"
        "<<<USER_REQUIREMENTS>>>\n"
        f"{user_requirements or '（未提供）'}\n"
        "<<<END_USER_REQUIREMENTS>>>\n"
        "已结构化文档：\n"
        "<<<SOURCE_DOCUMENT>>>\n"
        f"{source_document}\n"
        "<<<END_SOURCE_DOCUMENT>>>\n"
    )


def run_identify_entities_agent(
    *,
    source_document: str,
    counting_scope: str = "",
    user_requirements: str = "",
    session: Session,
    config_id: UUID | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    request_id: str | None = None,
) -> IdentifyEntitiesAgentResult:
    normalized_document = _normalize_required_text(
        source_document,
        name="source_document",
        max_length=_MAX_SOURCE_DOCUMENT_LENGTH,
    )
    normalized_scope = _normalize_optional_text(
        counting_scope, name="counting_scope", max_length=_MAX_CONTEXT_FIELD_LENGTH
    )
    normalized_requirements = _normalize_optional_text(
        user_requirements,
        name="user_requirements",
        max_length=_MAX_CONTEXT_FIELD_LENGTH,
    )

    system_prompt = load_identify_entities_prompt()
    prompt_path, prompt_hash = get_identify_entities_prompt_snapshot()
    client = get_llm_service_client(config_id=config_id, session=session)

    try:
        result, error = client.chat(
            prompt=build_identify_entities_user_prompt(
                source_document=normalized_document,
                counting_scope=normalized_scope,
                user_requirements=normalized_requirements,
            ),
            system_prompt=system_prompt,
            model=_normalize_optional_model(model),
            temperature=_DEFAULT_TEMPERATURE if temperature is None else temperature,
            max_tokens=max_tokens,
            request_id=request_id,
            caller_service=_CALLER_SERVICE_NAME,
        )
    except (LlmChatPersistenceError, LlmConfigError, IdentifyEntitiesPromptError):
        raise

    if error is not None or result is None:
        raise IdentifyEntitiesAgentError(error or "identify_entities agent failed")

    try:
        parsed = parse_object(result.text)
    except LlmJsonParseError as exc:
        raise IdentifyEntitiesAgentError(
            f"identify_entities agent returned invalid json: {exc}"
        ) from exc

    entities = _parse_entities_payload(parsed)

    return IdentifyEntitiesAgentResult(
        entities=entities,
        model=result.model,
        request_id=result.request_id,
        usage=result.usage,
        effective_config_id=client.config_id,
        effective_config_code=client.config_code,
        prompt_path=prompt_path,
        prompt_hash=prompt_hash,
    )


# ---------------------------------------------------------------------------
# Pipeline Step
# ---------------------------------------------------------------------------


class IdentifyEntitiesStep:
    """子任务 1.1 的 PipelineStep 薄包装。"""

    name = "ifpug.s1_1_identify_entities"

    def run(self, ctx: IfpugContext) -> StepRecord:
        if ctx.session is None:
            raise IdentifyEntitiesAgentError("ifpug pipeline requires ctx.session")

        result = run_identify_entities_agent(
            source_document=ctx.source_document,
            counting_scope=ctx.counting_scope,
            user_requirements=ctx.user_requirements,
            session=ctx.session,
            config_id=ctx.config_id,
            model=ctx.model,
            temperature=ctx.temperature,
            max_tokens=ctx.max_tokens,
            request_id=ctx.base.request_id,
        )

        # 在代码侧分配稳定 id 后再写回 ctx，确保下游步骤可以稳定引用。
        attached_entities: list[DataEntity] = []
        for raw in result.entities:
            entity_id = ctx.next_entity_id()
            attached_entities.append(
                DataEntity(
                    id=entity_id,
                    name=raw.name,
                    description=raw.description,
                    attributes=list(raw.attributes),
                    source_refs=list(raw.source_refs),
                )
            )
        ctx.candidate_entities.extend(attached_entities)

        return StepRecord(
            name=self.name,
            status=StepStatus.SUCCEEDED,
            model=result.model,
            request_id=result.request_id,
            effective_config_id=result.effective_config_id,
            effective_config_code=result.effective_config_code,
            prompt_path=result.prompt_path,
            prompt_hash=result.prompt_hash,
            usage=result.usage,
            metrics={
                "entities_in": 0,
                "entities_out": len(attached_entities),
                "entities_excluded": 0,
            },
        )
