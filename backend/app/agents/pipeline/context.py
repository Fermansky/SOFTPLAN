"""Pipeline Context 基础类型，所有流水线共享。

设计要点：
- `BasePipelineContext` 仅持有跨切关注点字段（run_id、abort 标记、
  step 记录、累积 usage）。业务流水线通过 `base: BasePipelineContext`
  字段组合使用，而不是继承，从而让领域 Context 保持扁平 dataclass、
  易于序列化。
- `StepRecord` 记录复现/审计单个步骤执行所需的全部信息：状态、耗时、
  模型 / Prompt 指纹和 token 用量。它被刻意设计为 JSON 友好（不放
  Session、Client 这类对象）。
- `LlmUsage` 来自 `llm_service`，是 `frozen` 的，因此这里提供
  `zero_usage()` 与 `sum_usage(a, b)` 工具函数，避免就地修改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID

from ...services import LlmUsage


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


def zero_usage() -> LlmUsage:
    """返回一个全零的 LlmUsage 实例。"""
    return LlmUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)


def sum_usage(a: LlmUsage, b: LlmUsage) -> LlmUsage:
    """返回 `a` 与 `b` 按字段相加得到的新 LlmUsage。"""
    return LlmUsage(
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
        total_tokens=a.total_tokens + b.total_tokens,
    )


@dataclass
class StepRecord:
    """单次流水线步骤执行的结构化记录。"""

    name: str
    status: StepStatus = StepStatus.PENDING
    started_at_ms: float | None = None
    elapsed_ms: int | None = None
    # LLM 指纹（可选；仅当步骤实际调用了 LLM 时由步骤填写）
    model: str | None = None
    request_id: str | None = None
    effective_config_id: UUID | None = None
    effective_config_code: str | None = None
    prompt_path: str | None = None
    prompt_hash: str | None = None
    usage: LlmUsage = field(default_factory=zero_usage)
    # 步骤自由记录的诊断信息（计数、决策摘要等）
    metrics: dict[str, Any] = field(default_factory=dict)
    # 状态为 FAILED 时存放错误信息；SKIPPED 时存放跳过原因。
    error: str | None = None
    skip_reason: str | None = None


@dataclass
class BasePipelineContext:
    """每次流水线运行共享的跨切上下文。

    业务 Context 应将此对象作为字段持有，并通过它来承载流水线层面的
    通用状态（step 记录、累积 usage、abort 标记等）。
    """

    run_id: str | None = None
    request_id: str | None = None

    step_records: list[StepRecord] = field(default_factory=list)
    total_usage: LlmUsage = field(default_factory=zero_usage)

    aborted: bool = False
    abort_reason: str | None = None
    aborted_step: str | None = None

    def add_step_record(self, record: StepRecord) -> None:
        self.step_records.append(record)
        self.total_usage = sum_usage(self.total_usage, record.usage)
