"""子任务 1.3：识别语义重复的候选实体并执行合并。

输入（来自 ctx）：
- 当前活跃候选实体（``ctx.active_entities()``）。
- ``counting_scope`` / ``user_requirements``：与前置步骤共用的上下文文本。

LLM 输出：等价组列表（每组 ≥ 2 个 id + canonical_name + rationale）。
代码侧合并语义（关键 —— 不能让 LLM 决定）：
- **并查集传递闭包**：即便 LLM 给出的等价组在 id 维度有重叠（``{E1,E2}`` 与
  ``{E2,E3}``），我们也会把它们合并成 ``{E1,E2,E3}``。
- **canonical 选举规则**：合并组中 **id 字典序最小** 的实体作为 canonical，
  其余打 ``EXCLUDED_BY_DUPLICATE`` 标签并被合并。**LLM 给出的
  canonical_name 仅作为辅助记入 rationale，不会覆盖 canonical 自身的 name**。
- **属性 / source_refs 并集**：被合并实体的 ``attributes`` 与
  ``source_refs`` 按 **名字（attr）/ quote（ref）** 去重后并入 canonical。
- **关系沉淀**：每个被合并实体写一条
  ``EntityRelation(from_id=被合并, to_id=canonical, relation_type="duplicate_of")``，
  便于审计追溯。

设计要点（**不删除原则**）：
- ``candidate_entities`` 列表的成员与顺序不变；被合并实体只是被打标签。
- LLM 输出中的非法情况（id 未知、单 id 组、跨组重复使用 id）写入
  ``metrics.warnings`` 而**不阻断**步骤——分类型决策容错优先。
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
from ..domain import (
    EXCLUDED_BY_DUPLICATE,
    Attribute,
    DataEntity,
    EntityRelation,
    Exclusion,
    SourceRef,
)

logger = logging.getLogger(__name__)

_CALLER_SERVICE_NAME = "backend.agent.ifpug.s1_3_merge_duplicates"
_DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "prompts"
    / "ifpug_s1_3_merge_duplicates.txt"
)
_PROMPT_ENV_VAR = "IFPUG_S1_3_MERGE_DUPLICATES_PROMPT_PATH"
_DEFAULT_TEMPERATURE = 0.1
_STEP_NAME = "ifpug.s1_3_merge_duplicates"
_MAX_ID_LENGTH = 64
_MAX_NAME_LENGTH = 200
_MAX_RATIONALE_LENGTH = 600
_MAX_CONTEXT_FIELD_LENGTH = 8000


# ---------------------------------------------------------------------------
# 领域错误
# ---------------------------------------------------------------------------


class MergeDuplicatesPromptError(RuntimeError):
    """读取 system prompt 文件失败时抛出。"""


class MergeDuplicatesAgentError(RuntimeError):
    """子任务 1.3 无法完成时抛出。"""


# ---------------------------------------------------------------------------
# Prompt 加载
# ---------------------------------------------------------------------------


_prompt_loader = PromptLoader(
    default_path=_DEFAULT_PROMPT_PATH,
    env_var=_PROMPT_ENV_VAR,
    error_cls=MergeDuplicatesPromptError,
    label="ifpug s1_3",
)


def _resolve_prompt_path() -> Path:
    return _prompt_loader.resolve_path()


load_merge_duplicates_prompt = _prompt_loader.cached_loader


def get_merge_duplicates_prompt_snapshot() -> tuple[str, str | None]:
    return _prompt_loader.snapshot()


# ---------------------------------------------------------------------------
# Agent 结果数据
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EquivalenceGroupProposal:
    """LLM 给出的一组同义实体建议。"""

    members: tuple[str, ...]
    canonical_name: str
    rationale: str


@dataclass(frozen=True)
class MergeDuplicatesAgentResult:
    groups: list[EquivalenceGroupProposal]
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
        raise MergeDuplicatesAgentError(
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
        raise MergeDuplicatesAgentError(
            f"merge_duplicates agent returned invalid field: {field_name}"
        )
    normalized = payload.strip()
    if not normalized:
        raise MergeDuplicatesAgentError(
            f"merge_duplicates agent returned empty field: {field_name}"
        )
    if len(normalized) > max_length:
        raise MergeDuplicatesAgentError(
            f"merge_duplicates agent returned field exceeding limit: {field_name}>{max_length}"
        )
    return normalized


def _parse_groups_payload(payload: dict[str, Any]) -> list[EquivalenceGroupProposal]:
    groups_raw = payload.get("groups")
    if not isinstance(groups_raw, list):
        raise MergeDuplicatesAgentError(
            "merge_duplicates agent returned invalid field: groups"
        )

    groups: list[EquivalenceGroupProposal] = []
    for group_index, raw in enumerate(groups_raw):
        if not isinstance(raw, dict):
            raise MergeDuplicatesAgentError(
                f"merge_duplicates agent returned invalid item at groups[{group_index}]"
            )
        members_raw = raw.get("members")
        if not isinstance(members_raw, list) or len(members_raw) < 2:
            raise MergeDuplicatesAgentError(
                f"merge_duplicates agent returned invalid members at groups[{group_index}]"
            )

        members: list[str] = []
        seen: set[str] = set()
        for member_index, member in enumerate(members_raw):
            member_id = _ensure_string(
                member,
                field_name=f"groups[{group_index}].members[{member_index}]",
                max_length=_MAX_ID_LENGTH,
            )
            if member_id in seen:
                # 同一组内重复 id 直接去重（rationale 里仍只算一次成员）。
                continue
            seen.add(member_id)
            members.append(member_id)
        if len(members) < 2:
            raise MergeDuplicatesAgentError(
                f"merge_duplicates agent returned degenerate group at groups[{group_index}]"
            )

        canonical_name = _ensure_string(
            raw.get("canonical_name"),
            field_name=f"groups[{group_index}].canonical_name",
            max_length=_MAX_NAME_LENGTH,
        )
        rationale = _ensure_string(
            raw.get("rationale"),
            field_name=f"groups[{group_index}].rationale",
            max_length=_MAX_RATIONALE_LENGTH,
        )

        groups.append(
            EquivalenceGroupProposal(
                members=tuple(members),
                canonical_name=canonical_name,
                rationale=rationale,
            )
        )
    return groups


# ---------------------------------------------------------------------------
# User Prompt 构建与 Agent 主入口
# ---------------------------------------------------------------------------


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


def build_merge_duplicates_user_prompt(
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


def run_merge_duplicates_agent(
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
) -> MergeDuplicatesAgentResult:
    if len(entities) < 2:
        raise MergeDuplicatesAgentError(
            "merge_duplicates agent requires at least two candidate entities"
        )

    normalized_scope = _normalize_optional_text(
        counting_scope, name="counting_scope", max_length=_MAX_CONTEXT_FIELD_LENGTH
    )
    normalized_requirements = _normalize_optional_text(
        user_requirements,
        name="user_requirements",
        max_length=_MAX_CONTEXT_FIELD_LENGTH,
    )

    system_prompt = load_merge_duplicates_prompt()
    prompt_path, prompt_hash = get_merge_duplicates_prompt_snapshot()
    client = get_llm_service_client(config_id=config_id, session=session)

    try:
        result, error = client.chat(
            prompt=build_merge_duplicates_user_prompt(
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
    except (LlmChatPersistenceError, LlmConfigError, MergeDuplicatesPromptError):
        raise

    if error is not None or result is None:
        raise MergeDuplicatesAgentError(error or "merge_duplicates agent failed")

    try:
        parsed = parse_object(result.text)
    except LlmJsonParseError as exc:
        raise MergeDuplicatesAgentError(
            f"merge_duplicates agent returned invalid json: {exc}"
        ) from exc

    groups = _parse_groups_payload(parsed)

    return MergeDuplicatesAgentResult(
        groups=groups,
        model=result.model,
        request_id=result.request_id,
        usage=result.usage,
        effective_config_id=client.config_id,
        effective_config_code=client.config_code,
        prompt_path=prompt_path,
        prompt_hash=prompt_hash,
    )


# ---------------------------------------------------------------------------
# 并查集 & 合并应用
# ---------------------------------------------------------------------------


class _UnionFind:
    """最小化并查集：仅支持 ``union`` 与 ``find``，按 id 字典序选 root。

    用 ``min(a, b)`` 作为合并后的 root，从而 ``canonical_id`` 一定是组里
    id 字典序最小的元素，与下游"canonical 选举规则"一致。
    """

    def __init__(self, items: list[str]) -> None:
        self._parent: dict[str, str] = {item: item for item in items}

    def find(self, x: str) -> str:
        # 路径压缩
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # 选字典序较小的为新 root，保证 canonical 稳定。
        if ra < rb:
            self._parent[rb] = ra
        else:
            self._parent[ra] = rb

    def groups(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for item in self._parent:
            root = self.find(item)
            result.setdefault(root, []).append(item)
        # 组内成员按 id 排序，输出确定性。
        for members in result.values():
            members.sort()
        return result


@dataclass
class _MergeOutcome:
    """合并应用后的统计信息。"""

    groups_proposed: int  # LLM 给出的原始组数
    groups_applied: int  # 实际产生合并（>= 2 个活跃成员）的组数
    entities_merged: int  # 被打 EXCLUDED_BY_DUPLICATE 的实体数
    unknown_ids: list[str]
    inactive_ids: list[str]  # LLM 引用的 id 是已被前置步骤排除的活跃实体之外
    canonical_ids: list[str]  # 实际作为 canonical 的实体 id 列表


def _apply_groups_to_ctx(
    ctx: IfpugContext,
    *,
    active_ids: set[str],
    groups: list[EquivalenceGroupProposal],
) -> _MergeOutcome:
    entity_by_id = {entity.id: entity for entity in ctx.candidate_entities}

    # 单次遍历同时完成：成员清洗、不合法 id 归类、有效组与 proposal 配对收集。
    valid_members: list[str] = []
    unknown_ids: list[str] = []
    inactive_ids: list[str] = []
    cleaned_pairs: list[tuple[list[str], EquivalenceGroupProposal]] = []

    for proposal in groups:
        members_this_group: list[str] = []
        for mid in proposal.members:
            if mid not in entity_by_id:
                unknown_ids.append(mid)
                continue
            if mid not in active_ids:
                inactive_ids.append(mid)
                continue
            members_this_group.append(mid)
        if len(members_this_group) >= 2:
            cleaned_pairs.append((members_this_group, proposal))
            for mid in members_this_group:
                if mid not in valid_members:
                    valid_members.append(mid)

    # 没有任何可合并组：直接返回（仍可能有 warnings）。
    if not cleaned_pairs:
        return _MergeOutcome(
            groups_proposed=len(groups),
            groups_applied=0,
            entities_merged=0,
            unknown_ids=unknown_ids,
            inactive_ids=inactive_ids,
            canonical_ids=[],
        )

    uf = _UnionFind(valid_members)
    # 把 LLM 等价关系塞进并查集（传递闭包由 union 自动给出）。
    for cleaned, _proposal in cleaned_pairs:
        first = cleaned[0]
        for other in cleaned[1:]:
            uf.union(first, other)

    # 同时记一份"该 canonical 的合并原因/名字提示"。一个 canonical 可能由
    # 多个 LLM 组合并而成（传递闭包），因此 rationale 累加。
    rationale_by_canonical: dict[str, list[str]] = {}
    name_by_canonical: dict[str, list[str]] = {}
    for cleaned, proposal in cleaned_pairs:
        root = uf.find(cleaned[0])
        rationale_by_canonical.setdefault(root, []).append(proposal.rationale)
        name_by_canonical.setdefault(root, []).append(proposal.canonical_name)

    grouped = uf.groups()
    groups_applied = 0
    entities_merged = 0
    canonical_ids: list[str] = []

    for canonical_id, members in grouped.items():
        if len(members) < 2:
            continue
        groups_applied += 1
        canonical_ids.append(canonical_id)
        canonical_entity = entity_by_id[canonical_id]

        # 合并属性 / source_refs 到 canonical（按 name / quote 去重）。
        existing_attr_names = {attr.name for attr in canonical_entity.attributes}
        existing_quote_keys = {
            (ref.quote, ref.location) for ref in canonical_entity.source_refs
        }

        rationale_text = "；".join(
            rationale_by_canonical.get(canonical_id, [])
        ) or "(no rationale provided)"
        canonical_name_hint = (
            name_by_canonical.get(canonical_id, [None])[0] or canonical_entity.name
        )

        for mid in members:
            if mid == canonical_id:
                continue
            other = entity_by_id[mid]
            # 不删除：仅打 Exclusion + 记关系。
            other.exclusions.append(
                Exclusion(
                    tag=EXCLUDED_BY_DUPLICATE,
                    rationale=(
                        f"merged into {canonical_id} ({canonical_name_hint}); {rationale_text}"
                    ),
                    step=_STEP_NAME,
                )
            )
            ctx.relations.append(
                EntityRelation(
                    from_id=mid,
                    to_id=canonical_id,
                    relation_type="duplicate_of",
                    rationale=rationale_text,
                )
            )

            for attr in other.attributes:
                if attr.name not in existing_attr_names:
                    canonical_entity.attributes.append(
                        Attribute(
                            name=attr.name,
                            description=attr.description,
                            is_user_required=attr.is_user_required,
                            is_foreign_key=attr.is_foreign_key,
                        )
                    )
                    existing_attr_names.add(attr.name)
            for ref in other.source_refs:
                key = (ref.quote, ref.location)
                if key not in existing_quote_keys:
                    canonical_entity.source_refs.append(
                        SourceRef(quote=ref.quote, location=ref.location)
                    )
                    existing_quote_keys.add(key)

            entities_merged += 1

    canonical_ids.sort()
    return _MergeOutcome(
        groups_proposed=len(groups),
        groups_applied=groups_applied,
        entities_merged=entities_merged,
        unknown_ids=unknown_ids,
        inactive_ids=inactive_ids,
        canonical_ids=canonical_ids,
    )


# ---------------------------------------------------------------------------
# Pipeline Step
# ---------------------------------------------------------------------------


class MergeDuplicatesStep:
    """子任务 1.3 的 PipelineStep 薄包装。"""

    name = _STEP_NAME

    def run(self, ctx: IfpugContext) -> StepRecord:
        if ctx.session is None:
            raise MergeDuplicatesAgentError("ifpug pipeline requires ctx.session")

        active = ctx.active_entities()
        active_count = len(active)

        if active_count < 2:
            logger.info(
                "ifpug s1_3 skipped: fewer than 2 active entities (got %d)", active_count
            )
            return StepRecord(
                name=self.name,
                status=StepStatus.SKIPPED,
                skip_reason="fewer than 2 active candidate entities",
                metrics={
                    "entities_in": active_count,
                    "groups_proposed": 0,
                    "groups_applied": 0,
                    "entities_merged": 0,
                    "entities_out": active_count,
                },
            )

        result = run_merge_duplicates_agent(
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

        outcome = _apply_groups_to_ctx(
            ctx,
            active_ids={entity.id for entity in active},
            groups=result.groups,
        )

        metrics: dict[str, Any] = {
            "entities_in": active_count,
            "groups_proposed": outcome.groups_proposed,
            "groups_applied": outcome.groups_applied,
            "entities_merged": outcome.entities_merged,
            "entities_out": active_count - outcome.entities_merged,
            "canonical_ids": outcome.canonical_ids,
        }
        if outcome.unknown_ids or outcome.inactive_ids:
            metrics["warnings"] = {
                "unknown_ids": outcome.unknown_ids,
                "inactive_ids": outcome.inactive_ids,
            }
            logger.warning(
                "ifpug s1_3 received anomalous members: unknown=%s inactive=%s",
                outcome.unknown_ids,
                outcome.inactive_ids,
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
