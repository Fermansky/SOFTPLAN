"""通用 Agent Pipeline 框架。

本包提供一套最小、可复用的脚手架，用于把多个由 LLM 驱动的 Agent
组合成一条顺序执行的流水线，并通过一个共享的可变 Context 在步骤之间
传递数据。

本框架与具体业务无关：领域特定的 Context（如 IFPUG）应通过组合
`BasePipelineContext`（作为字段持有），而不是把业务字段继承到这里。
"""

from .base import (
    PipelineAbort,
    PipelineStep,
    PipelineStepError,
)
from .context import (
    BasePipelineContext,
    StepRecord,
    StepStatus,
    sum_usage,
    zero_usage,
)
from .runner import AgentPipeline, run_pipeline

__all__ = [
    "AgentPipeline",
    "BasePipelineContext",
    "PipelineAbort",
    "PipelineStep",
    "PipelineStepError",
    "StepRecord",
    "StepStatus",
    "run_pipeline",
    "sum_usage",
    "zero_usage",
]
