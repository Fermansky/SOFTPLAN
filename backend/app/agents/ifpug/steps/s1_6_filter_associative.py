"""子任务 1.6：识别"关联实体（associative entity）"并打 Exclusion 标签。

形态与 s1_2 / s1_4 / s1_5 完全一致（分类型 / 打包调用 / 仅追加 Exclusion），
区别仅在 prompt 判断口径与标签 tag。详见 prompt 文件
``ifpug_s1_6_filter_associative.txt``。
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
from ..domain import EXCLUDED_BY_ASSOCIATIVE, DataEntity, Exclusion

logger = logging.getLogger(__name__)

_CALLER_SERVICE_NAME = "backend.agent.ifpug.s1_6_filter_associative"
_DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "prompts"
    / "ifpug_s1_6_filter_associative.txt"
)
_PROMPT_ENV_VAR = "IFPUG_S1_6_FILTER_ASSOCIATIVE_PROMPT_PATH"
_DEFAULT_TEMPERATURE = 0.1
_STEP_NAME = "ifpug.s1_6_filter_associative"
_TAG = EXCLUDED_BY_ASSOCIATIVE
_MAX_ID_LENGTH = 64
_MAX_RATIONALE_LENGTH = 600
_MAX_CONTEXT_FIELD_LENGTH = 8000


class FilterAssociativePromptError(RuntimeError):
    """读取 system prompt 文件失败时抛出。"""


class FilterAssociativeAgentError(RuntimeError):
    """子任务 1.6 无法完成时抛出。"""


_prompt_loader = PromptLoader(
    default_path=_DEFAULT_PROMPT_PATH,
    env_var=_PROMPT_ENV_VAR,
    error_cls=FilterAssociativePromptError,
    label="ifpug s1_6",
)


def _resolve_prompt_path() -> Path:
    return _prompt_loader.resolve_path()


load_filter_associative_prompt = _prompt_loader.cached_loader


def get_filter_associative_prompt_snapshot() -> tuple[str, str | None]:
    return _prompt_loader.snapshot()


@dataclass(frozen=True)
class ExclusionVerdict:
    entity_id: str
    rationale: str


@dataclass(frozen=True)
class FilterAssociativeAgentResult:
    verdicts: list[ExclusionVerdict]
    model: str
    request_id: str | None
    usage: LlmUsage
    effective_config_id: UUID | None
    effective_config_code: str | None
    prompt_path: str
    prompt_hash: str | None


def _normalize_optional_text(value: str | None, *, name: str, max_length: int) -> str:
    if value is None:
        return ""
    normalized = value.strip()
    if len(normalized) > max_length:
        raise FilterAssociativeAgentError(
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
        raise FilterAssociativeAgentError(
            f"filter_associative agent returned invalid field: {field_name}"
        )
    normalized = payload.strip()
    if not normalized:
        raise FilterAssociativeAgentError(
            f"filter_associative agent returned empty field: {field_name}"
        )
    if len(normalized) > max_length:
        raise FilterAssociativeAgentError(
            f"filter_associative agent returned field exceeding limit: {field_name}>{max_length}"
        )
    return normalized


def _parse_verdicts_payload(payload: dict[str, Any]) -> list[ExclusionVerdict]:
    excluded_raw = payload.get("excluded")
    if not isinstance(excluded_raw, list):
        raise FilterAssociativeAgentError(
            "filter_associative agent returned invalid field: excluded"
        )

    verdicts: list[ExclusionVerdict] = []
    for index, raw in enumerate(excluded_raw):
        if not isinstance(raw, dict):
            raise FilterAssociativeAgentError(
                f"filter_associative agent returned invalid item at excluded[{index}]"
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


def _summarize_entity_for_prompt(entity: DataEntity) -> dict[str, Any]:
    return {
        "id": entity.id,
        "name": entity.name,
        "description": entity.description,
        "attributes": [
            {"name": attr.name, "description": attr.description or ""}
            for attr in entity.attributes
        ],
    }


def build_filter_associative_user_prompt(
    *,
    entities: list[DataEntity],
    counting_scope: str,
    user_requirements: str,
) -> str:
    import json

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


def run_filter_associative_agent(
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
) -> FilterAssociativeAgentResult:
    if not entities:
        raise FilterAssociativeAgentError(
            "filter_associative agent requires at least one candidate entity"
        )

    normalized_scope = _normalize_optional_text(
        counting_scope, name="counting_scope", max_length=_MAX_CONTEXT_FIELD_LENGTH
    )
    normalized_requirements = _normalize_optional_text(
        user_requirements,
        name="user_requirements",
        max_length=_MAX_CONTEXT_FIELD_LENGTH,
    )

    system_prompt = load_filter_associative_prompt()
    prompt_path, prompt_hash = get_filter_associative_prompt_snapshot()
    client = get_llm_service_client(config_id=config_id, session=session)

    try:
        result, error = client.chat(
            prompt=build_filter_associative_user_prompt(
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
    except (LlmChatPersistenceError, LlmConfigError, FilterAssociativePromptError):
        raise

    if error is not None or result is None:
        raise FilterAssociativeAgentError(error or "filter_associative agent failed")

    try:
        parsed = parse_object(result.text)
    except LlmJsonParseError as exc:
        raise FilterAssociativeAgentError(
            f"filter_associative agent returned invalid json: {exc}"
        ) from exc

    verdicts = _parse_verdicts_payload(parsed)

    return FilterAssociativeAgentResult(
        verdicts=verdicts,
        model=result.model,
        request_id=result.request_id,
        usage=result.usage,
        effective_config_id=client.config_id,
        effective_config_code=client.config_code,
        prompt_path=prompt_path,
        prompt_hash=prompt_hash,
    )


@dataclass
class _ApplyVerdictsOutcome:
    applied: int
    unknown_ids: list[str]
    duplicate_ids: list[str]
    already_excluded_ids: list[str]


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
            if eid in entity_by_id:
                already_excluded_ids.append(eid)
            else:
                unknown_ids.append(eid)
            continue

        entity_by_id[eid].exclusions.append(
            Exclusion(
                tag=_TAG,
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


class FilterAssociativeStep:
    """子任务 1.6 的 PipelineStep 薄包装。"""

    name = _STEP_NAME

    def run(self, ctx: IfpugContext) -> StepRecord:
        if ctx.session is None:
            raise FilterAssociativeAgentError("ifpug pipeline requires ctx.session")

        active = ctx.active_entities()
        active_count = len(active)

        if not active:
            logger.info("ifpug s1_6 skipped: no active candidate entities")
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

        result = run_filter_associative_agent(
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
        if outcome.unknown_ids or outcome.duplicate_ids or outcome.already_excluded_ids:
            metrics["warnings"] = {
                "unknown_ids": outcome.unknown_ids,
                "duplicate_ids": outcome.duplicate_ids,
                "already_excluded_ids": outcome.already_excluded_ids,
            }
            logger.warning(
                "ifpug s1_6 received anomalous verdicts: unknown=%s duplicates=%s already_excluded=%s",
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
