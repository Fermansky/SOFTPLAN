"""IFPUG 流水线的临时调试路由。

当前阶段（PR2）仅暴露逻辑文件识别流水线的 debug 端点，用于在主流程
集成前快速验证各子任务的产出与 prompt 调优效果。返回完整的 ctx 快照
以便 UI / 调试侧画"漏斗"。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from ...agents.ifpug import (
    FilterUnmaintainedAgentError,
    FilterUnmaintainedPromptError,
    IdentifyEntitiesAgentError,
    IdentifyEntitiesPromptError,
    IfpugContext,
    MergeDuplicatesAgentError,
    MergeDuplicatesPromptError,
    build_logical_file_pipeline,
    list_registered_step_names,
)
from ...agents.pipeline import PipelineStepError, StepRecord
from ...database import get_session
from ...services import (
    LlmChatPersistenceError,
    LlmConfigConflictError,
    LlmConfigDisabledError,
    LlmConfigNotFoundError,
    LlmConfigResolutionError,
    LlmConfigValidationError,
)

router = APIRouter(prefix="/agents/ifpug", tags=["agents", "ifpug"])


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class IfpugDebugRunRequest(BaseModel):
    source_document: str = Field(min_length=1, max_length=200000)
    counting_scope: str = Field(default="", max_length=8000)
    user_requirements: str = Field(default="", max_length=8000)
    config_id: UUID | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    request_id: str | None = None
    until: str | None = Field(
        default=None,
        description="按子任务短名截断流水线执行（如 's1_1' / 's1_2' / 's1_3'）。None 表示跑全部。",
    )


class IfpugLlmUsageRead(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class IfpugStepRecordRead(BaseModel):
    name: str
    status: str
    elapsed_ms: int | None = None
    model: str | None = None
    request_id: str | None = None
    effective_config_id: UUID | None = None
    effective_config_code: str | None = None
    prompt_path: str | None = None
    prompt_hash: str | None = None
    usage: IfpugLlmUsageRead = IfpugLlmUsageRead()
    metrics: dict[str, Any] = {}
    error: str | None = None
    skip_reason: str | None = None


class IfpugSourceRefRead(BaseModel):
    quote: str
    location: str | None = None


class IfpugAttributeRead(BaseModel):
    name: str
    description: str | None = None
    is_user_required: bool | None = None
    is_foreign_key: bool | None = None


class IfpugExclusionRead(BaseModel):
    tag: str
    rationale: str
    step: str


class IfpugDataEntityRead(BaseModel):
    id: str
    name: str
    description: str = ""
    attributes: list[IfpugAttributeRead] = []
    source_refs: list[IfpugSourceRefRead] = []
    exclusions: list[IfpugExclusionRead] = []


class IfpugEntityRelationRead(BaseModel):
    from_id: str
    to_id: str
    relation_type: str
    rationale: str = ""


class IfpugDebugRunRead(BaseModel):
    counting_scope: str
    user_requirements: str
    candidate_entities: list[IfpugDataEntityRead]
    active_entity_ids: list[str]
    relations: list[IfpugEntityRelationRead] = []
    step_records: list[IfpugStepRecordRead]
    total_usage: IfpugLlmUsageRead
    aborted: bool
    abort_reason: str | None = None
    aborted_step: str | None = None
    registered_steps: list[str]


# ---------------------------------------------------------------------------
# 错误映射
# ---------------------------------------------------------------------------


def _raise_llm_config_http_error(exc: Exception) -> None:
    if isinstance(exc, LlmConfigNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigDisabledError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigResolutionError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    if isinstance(exc, LlmConfigValidationError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _map_step_agent_error(detail: str) -> HTTPException:
    """把 step 内部抛出的 ``XxxAgentError`` 映射到 HTTP 状态码。

    - 入参类问题（用户给的 counting_scope / user_requirements 太长等）→ 422
    - 其它（LLM 返回结构异常、字段越界、调用失败等）→ 502
    """
    invalid_input_keywords = (
        "source_document",
        "counting_scope",
        "user_requirements",
    )
    if any(keyword in detail for keyword in invalid_input_keywords):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _serialize_step_record(record: StepRecord) -> IfpugStepRecordRead:
    return IfpugStepRecordRead(
        name=record.name,
        status=record.status.value,
        elapsed_ms=record.elapsed_ms,
        model=record.model,
        request_id=record.request_id,
        effective_config_id=record.effective_config_id,
        effective_config_code=record.effective_config_code,
        prompt_path=record.prompt_path,
        prompt_hash=record.prompt_hash,
        usage=IfpugLlmUsageRead(
            prompt_tokens=record.usage.prompt_tokens,
            completion_tokens=record.usage.completion_tokens,
            total_tokens=record.usage.total_tokens,
        ),
        metrics=dict(record.metrics),
        error=record.error,
        skip_reason=record.skip_reason,
    )


def _serialize_context(ctx: IfpugContext) -> IfpugDebugRunRead:
    return IfpugDebugRunRead(
        counting_scope=ctx.counting_scope,
        user_requirements=ctx.user_requirements,
        candidate_entities=[
            IfpugDataEntityRead(
                id=entity.id,
                name=entity.name,
                description=entity.description,
                attributes=[
                    IfpugAttributeRead(
                        name=attr.name,
                        description=attr.description,
                        is_user_required=attr.is_user_required,
                        is_foreign_key=attr.is_foreign_key,
                    )
                    for attr in entity.attributes
                ],
                source_refs=[
                    IfpugSourceRefRead(quote=ref.quote, location=ref.location)
                    for ref in entity.source_refs
                ],
                exclusions=[
                    IfpugExclusionRead(tag=ex.tag, rationale=ex.rationale, step=ex.step)
                    for ex in entity.exclusions
                ],
            )
            for entity in ctx.candidate_entities
        ],
        active_entity_ids=[entity.id for entity in ctx.active_entities()],
        relations=[
            IfpugEntityRelationRead(
                from_id=rel.from_id,
                to_id=rel.to_id,
                relation_type=rel.relation_type,
                rationale=rel.rationale,
            )
            for rel in ctx.relations
        ],
        step_records=[_serialize_step_record(r) for r in ctx.base.step_records],
        total_usage=IfpugLlmUsageRead(
            prompt_tokens=ctx.base.total_usage.prompt_tokens,
            completion_tokens=ctx.base.total_usage.completion_tokens,
            total_tokens=ctx.base.total_usage.total_tokens,
        ),
        aborted=ctx.base.aborted,
        abort_reason=ctx.base.abort_reason,
        aborted_step=ctx.base.aborted_step,
        registered_steps=list_registered_step_names(),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/logical-file/debug-run", response_model=IfpugDebugRunRead)
def debug_run_logical_file_pipeline(
    payload: IfpugDebugRunRequest,
    session: Session = Depends(get_session),
) -> IfpugDebugRunRead:
    try:
        pipeline = build_logical_file_pipeline(until=payload.until)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    ctx = IfpugContext(
        source_document=payload.source_document,
        counting_scope=payload.counting_scope,
        user_requirements=payload.user_requirements,
        session=session,
        config_id=payload.config_id,
        model=payload.model,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
    )
    ctx.base.request_id = payload.request_id

    try:
        pipeline.run(ctx)
    except PipelineStepError as exc:
        # 解包 runner 抛出的包装异常，根据原始原因映射到 HTTP。
        original = exc.__cause__ if exc.__cause__ is not None else exc
        _raise_llm_config_http_error(original)
        prompt_errors = (
            IdentifyEntitiesPromptError,
            FilterUnmaintainedPromptError,
            MergeDuplicatesPromptError,
        )
        if isinstance(original, prompt_errors) or isinstance(original, LlmChatPersistenceError):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(original),
            ) from exc
        step_agent_errors = (
            IdentifyEntitiesAgentError,
            FilterUnmaintainedAgentError,
            MergeDuplicatesAgentError,
        )
        if isinstance(original, step_agent_errors):
            raise _map_step_agent_error(str(original)) from exc
        raise

    return _serialize_context(ctx)
