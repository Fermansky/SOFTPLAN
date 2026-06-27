"""子任务 1.2：识别"本应用不维护"的候选数据实体并打 Exclusion 标签。

输入（来自 ctx）：
- ``candidate_entities``：当前活跃的候选实体（``ctx.active_entities()``）。
- ``counting_scope`` / ``user_requirements``：与 s1_1 共用的上下文文本。

输出（写回 ctx）：
- 给被判定为"未被维护"的实体追加 ``Exclusion(EXCLUDED_BY_UNMAINTAINED, ...)``。
- 候选实体本身**不删除**，未列出的实体视为保留。

设计要点：
- **不删除原则**：只追加 Exclusion，不修改 ``ctx.candidate_entities`` 顺序与成员。
- **打包一次调用**：一次性把所有活跃候选送进 LLM，让其在全局视角下决策。
- **严格 schema 校验**：LLM 返回的 id 必须是入参中提供过的活跃 id；未知 id /
  对已被排除的实体重复打标签 / 重复 id 都会被记入 ``metrics.warnings``，但
  不会让整个步骤失败——同一个错误一旦阻断流水线，调试代价过高。
- **无活跃候选时短路**：直接返回 SKIPPED 的 StepRecord，不发起 LLM 调用。
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
from ..domain import EXCLUDED_BY_UNMAINTAINED, DataEntity, Exclusion

logger = logging.getLogger(__name__)

_CALLER_SERVICE_NAME = "backend.agent.ifpug.s1_2_filter_unmaintained"
_DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "prompts"
    / "ifpug_s1_2_filter_unmaintained.txt"
)
_PROMPT_ENV_VAR = "IFPUG_S1_2_FILTER_UNMAINTAINED_PROMPT_PATH"
_DEFAULT_TEMPERATURE = 0.1
_STEP_NAME = "ifpug.s1_2_filter_unmaintained"
_MAX_ID_LENGTH = 64
_MAX_RATIONALE_LENGTH = 600
_MAX_CONTEXT_FIELD_LENGTH = 8000


# ---------------------------------------------------------------------------
# 领域错误
# ---------------------------------------------------------------------------


class FilterUnmaintainedPromptError(RuntimeError):
    """读取 system prompt 文件失败时抛出。"""


class FilterUnmaintainedAgentError(RuntimeError):
    """子任务 1.2 无法完成时抛出。"""


# ---------------------------------------------------------------------------
# Prompt 加载（通过通用 PromptLoader 完成）
# ---------------------------------------------------------------------------


_prompt_loader = PromptLoader(
    default_path=_DEFAULT_PROMPT_PATH,
    env_var=_PROMPT_ENV_VAR,
    error_cls=FilterUnmaintainedPromptError,
    label="ifpug s1_2",
)


def _resolve_prompt_path() -> Path:
    return _prompt_loader.resolve_path()


load_filter_unmaintained_prompt = _prompt_loader.cached_loader


def get_filter_unmaintained_prompt_snapshot() -> tuple[str, str | None]:
    return _prompt_loader.snapshot()


# ---------------------------------------------------------------------------
# Agent 结果数据
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExclusionVerdict:
    """LLM 对单个候选实体给出的排除判定。"""

    entity_id: str
    rationale: str


@dataclass(frozen=True)
class FilterUnmaintainedAgentResult:
    """LLM 调用的原始返回结果。"""

    verdicts: list[ExclusionVerdict]
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


def _normalize_optional_text(value: str | None, *, name: str, max_length: int) -> str:
    if value is None:
        return ""
    normalized = value.strip()
    if len(normalized) > max_length:
        raise FilterUnmaintainedAgentError(
            f"{name} exceeds the {max_length} character limit"
        )
    return normalized


def _normalize_optional_model(model: str | None) -> str | None:
    if model is None:
        return None
    normalized = model.strip()
    return normalized or None


def _ensure_string(payload: Any, *, field_name: str, max_length: int) -> str:
    if not isinstance(payload, str):
        raise FilterUnmaintainedAgentError(
            f"filter_unmaintained agent returned invalid field: {field_name}"
        )
    normalized = payload.strip()
    if not normalized:
        raise FilterUnmaintainedAgentError(
            f"filter_unmaintained agent returned empty field: {field_name}"
        )
    if len(normalized) > max_length:
        raise FilterUnmaintainedAgentError(
            f"filter_unmaintained agent returned field exceeding limit: {field_name}>{max_length}"
        )
    return normalized


def _parse_verdicts_payload(payload: dict[str, Any]) -> list[ExclusionVerdict]:
    excluded_raw = payload.get("excluded")
    if not isinstance(excluded_raw, list):
        raise FilterUnmaintainedAgentError(
            "filter_unmaintained agent returned invalid field: excluded"
        )

    verdicts: list[ExclusionVerdict] = []
    for index, raw in enumerate(excluded_raw):
        if not isinstance(raw, dict):
            raise FilterUnmaintainedAgentError(
                f"filter_unmaintained agent returned invalid item at excluded[{index}]"
            )
        entity_id = _ensure_string(
            raw.get("id"),
            field_name=f"excluded[{index}].id",
            max_length=_MAX_ID_LENGTH,
        )
        rationale = _ensure_string(
            raw.get("rationale"),
            field_name=f"excluded[{index}].rationale",
            max_length=_MAX_RATIONALE_LENGTH,
        )
        verdicts.append(ExclusionVerdict(entity_id=entity_id, rationale=rationale))
    return verdicts


# ---------------------------------------------------------------------------
# User Prompt 构建与 Agent 主入口
# ---------------------------------------------------------------------------


def _summarize_entity_for_prompt(entity: DataEntity) -> dict[str, Any]:
    """把实体打成精简版字典，控制 prompt 长度（不含 exclusions / extra）。"""
    return {
        "id": entity.id,
        "name": entity.name,
        "description": entity.description,
        "attributes": [
            {"name": attr.name, "description": attr.description or ""}
            for attr in entity.attributes
        ],
    }


def build_filter_unmaintained_user_prompt(
    *,
    entities: list[DataEntity],
    counting_scope: str,
    user_requirements: str,
) -> str:
    import json

    # 紧凑 JSON：避免不必要的空白挤占 token
    entities_json = json.dumps(
        [_summarize_entity_for_prompt(e) for e in entities],
        ensure_ascii=False,
        separators=(",", ":"),
    )
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
        "候选实体（JSON 数组）：\n"
        "<<<CANDIDATE_ENTITIES>>>\n"
        f"{entities_json}\n"
        "<<<END_CANDIDATE_ENTITIES>>>\n"
    )


def run_filter_unmaintained_agent(
    *,
    entities: list[DataEntity],
    counting_scope: str = "",
    user_requirements: str = "",
    session: Session,
    config_id: UUID | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    request_id: str | None = None,
) -> FilterUnmaintainedAgentResult:
    if not entities:
        raise FilterUnmaintainedAgentError(
            "filter_unmaintained agent requires at least one candidate entity"
        )

    normalized_scope = _normalize_optional_text(
        counting_scope, name="counting_scope", max_length=_MAX_CONTEXT_FIELD_LENGTH
    )
    normalized_requirements = _normalize_optional_text(
        user_requirements,
        name="user_requirements",
        max_length=_MAX_CONTEXT_FIELD_LENGTH,
    )

    system_prompt = load_filter_unmaintained_prompt()
    prompt_path, prompt_hash = get_filter_unmaintained_prompt_snapshot()
    client = get_llm_service_client(config_id=config_id, session=session)

    try:
        result, error = client.chat(
            prompt=build_filter_unmaintained_user_prompt(
                entities=entities,
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
    except (LlmChatPersistenceError, LlmConfigError, FilterUnmaintainedPromptError):
        raise

    if error is not None or result is None:
        raise FilterUnmaintainedAgentError(error or "filter_unmaintained agent failed")

    try:
        parsed = parse_object(result.text)
    except LlmJsonParseError as exc:
        raise FilterUnmaintainedAgentError(
            f"filter_unmaintained agent returned invalid json: {exc}"
        ) from exc

    verdicts = _parse_verdicts_payload(parsed)

    return FilterUnmaintainedAgentResult(
        verdicts=verdicts,
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


@dataclass
class _ApplyVerdictsOutcome:
    """把 LLM 判定结果应用到 ctx 后的统计信息。"""

    applied: int  # 实际新增的 Exclusion 数量
    unknown_ids: list[str]  # LLM 返回了入参里没出现的 id
    duplicate_ids: list[str]  # LLM 在同一次返回里对同一个 id 给了多条判定
    already_excluded_ids: list[str]  # LLM 判定的 id 已被本步骤之前的步骤排除


def _apply_verdicts_to_ctx(
    ctx: IfpugContext,
    *,
    active_ids: set[str],
    verdicts: list[ExclusionVerdict],
) -> _ApplyVerdictsOutcome:
    entity_by_id = {entity.id: entity for entity in ctx.candidate_entities}

    applied = 0
    unknown_ids: list[str] = []
    duplicate_ids: list[str] = []
    already_excluded_ids: list[str] = []
    seen_in_this_run: set[str] = set()

    for verdict in verdicts:
        eid = verdict.entity_id
        if eid in seen_in_this_run:
            duplicate_ids.append(eid)
            continue
        seen_in_this_run.add(eid)

        if eid not in active_ids:
            # 既可能是 LLM 凭空编造 id，也可能是某个已被前置步骤排除的 id
            if eid in entity_by_id:
                already_excluded_ids.append(eid)
            else:
                unknown_ids.append(eid)
            continue

        entity_by_id[eid].exclusions.append(
            Exclusion(
                tag=EXCLUDED_BY_UNMAINTAINED,
                rationale=verdict.rationale,
                step=_STEP_NAME,
            )
        )
        applied += 1

    return _ApplyVerdictsOutcome(
        applied=applied,
        unknown_ids=unknown_ids,
        duplicate_ids=duplicate_ids,
        already_excluded_ids=already_excluded_ids,
    )


class FilterUnmaintainedStep:
    """子任务 1.2 的 PipelineStep 薄包装。"""

    name = _STEP_NAME

    def run(self, ctx: IfpugContext) -> StepRecord:
        if ctx.session is None:
            raise FilterUnmaintainedAgentError("ifpug pipeline requires ctx.session")

        active = ctx.active_entities()
        active_count = len(active)

        # 空集短路：跳过 LLM 调用，但仍记录一条 SKIPPED，便于审计漏斗。
        if not active:
            logger.info("ifpug s1_2 skipped: no active candidate entities")
            return StepRecord(
                name=self.name,
                status=StepStatus.SKIPPED,
                skip_reason="no active candidate entities",
                metrics={
                    "entities_in": 0,
                    "entities_excluded": 0,
                    "entities_out": 0,
                },
            )

        result = run_filter_unmaintained_agent(
            entities=active,
            counting_scope=ctx.counting_scope,
            user_requirements=ctx.user_requirements,
            session=ctx.session,
            config_id=ctx.config_id,
            model=ctx.model,
            temperature=ctx.temperature,
            max_tokens=ctx.max_tokens,
            request_id=ctx.base.request_id,
        )

        outcome = _apply_verdicts_to_ctx(
            ctx,
            active_ids={entity.id for entity in active},
            verdicts=result.verdicts,
        )

        metrics: dict[str, Any] = {
            "entities_in": active_count,
            "entities_excluded": outcome.applied,
            "entities_out": active_count - outcome.applied,
        }
        # 仅在异常情况存在时写入 warnings，避免 metrics 大量空字段。
        if outcome.unknown_ids or outcome.duplicate_ids or outcome.already_excluded_ids:
            metrics["warnings"] = {
                "unknown_ids": outcome.unknown_ids,
                "duplicate_ids": outcome.duplicate_ids,
                "already_excluded_ids": outcome.already_excluded_ids,
            }
            logger.warning(
                "ifpug s1_2 received anomalous verdicts: unknown=%s duplicates=%s already_excluded=%s",
                outcome.unknown_ids,
                outcome.duplicate_ids,
                outcome.already_excluded_ids,
            )

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
            metrics=metrics,
        )
