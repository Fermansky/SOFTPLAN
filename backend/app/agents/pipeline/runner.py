"""顺序执行的流水线 Runner。

Runner 被刻意设计得很小：
- 按顺序遍历步骤；
- 对每个步骤：计时、捕获错误、向 Context 追加 `StepRecord`；
- 遇到 abort（来自 `PipelineAbort` 或未预期异常）时短路后续步骤。

并发、重试、DAG 执行目前明确不在范围内。需要批处理或重试的步骤应在
其内部自行实现。
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Callable

from .base import PipelineAbort, PipelineStep, PipelineStepError
from .context import BasePipelineContext, StepRecord, StepStatus

logger = logging.getLogger(__name__)


# 给定一个领域 Context，返回其内部嵌入的 `BasePipelineContext`。
# 因为我们不强制业务 Context 继承基类，所以需要这么一个抽取器。
BaseExtractor = Callable[[object], BasePipelineContext]


def _default_base_extractor(ctx: object) -> BasePipelineContext:
    if isinstance(ctx, BasePipelineContext):
        return ctx
    base = getattr(ctx, "base", None)
    if isinstance(base, BasePipelineContext):
        return base
    raise TypeError(
        "pipeline context must be a BasePipelineContext or expose a `base` "
        "attribute of type BasePipelineContext"
    )


class AgentPipeline:
    """有序的步骤集合，针对同一个共享 Context 顺序执行。"""

    def __init__(
        self,
        steps: list[PipelineStep],
        *,
        base_extractor: BaseExtractor | None = None,
    ) -> None:
        if not steps:
            raise ValueError("AgentPipeline requires at least one step")
        self._steps = list(steps)
        self._base_extractor = base_extractor or _default_base_extractor

    @property
    def steps(self) -> list[PipelineStep]:
        return list(self._steps)

    def run(self, ctx: object) -> object:
        base = self._base_extractor(ctx)

        for step in self._steps:
            if base.aborted:
                logger.info(
                    "pipeline already aborted; skipping remaining steps step=%s reason=%s",
                    step.name,
                    base.abort_reason,
                )
                base.add_step_record(
                    StepRecord(
                        name=step.name,
                        status=StepStatus.SKIPPED,
                        skip_reason=f"pipeline aborted: {base.abort_reason}",
                    )
                )
                continue

            self._run_one(step, ctx, base)

        return ctx

    def _run_one(self, step: PipelineStep, ctx: object, base: BasePipelineContext) -> None:
        started = perf_counter()
        logger.info("pipeline step start step=%s", step.name)
        try:
            record = step.run(ctx)
        except PipelineAbort as exc:
            elapsed_ms = int((perf_counter() - started) * 1000)
            logger.info(
                "pipeline step aborted step=%s reason=%s elapsed_ms=%d",
                step.name,
                exc.reason,
                elapsed_ms,
            )
            base.aborted = True
            base.abort_reason = exc.reason
            base.aborted_step = step.name
            base.add_step_record(
                StepRecord(
                    name=step.name,
                    status=StepStatus.SKIPPED,
                    elapsed_ms=elapsed_ms,
                    skip_reason=exc.reason,
                )
            )
            return
        except Exception as exc:
            elapsed_ms = int((perf_counter() - started) * 1000)
            logger.warning(
                "pipeline step failed step=%s error=%s elapsed_ms=%d",
                step.name,
                exc,
                elapsed_ms,
            )
            base.aborted = True
            base.abort_reason = str(exc) or exc.__class__.__name__
            base.aborted_step = step.name
            base.add_step_record(
                StepRecord(
                    name=step.name,
                    status=StepStatus.FAILED,
                    elapsed_ms=elapsed_ms,
                    error=str(exc) or exc.__class__.__name__,
                )
            )
            raise PipelineStepError(step.name, base.abort_reason) from exc

        elapsed_ms = int((perf_counter() - started) * 1000)
        if not isinstance(record, StepRecord):
            base.aborted = True
            base.abort_reason = "step returned non-StepRecord value"
            base.aborted_step = step.name
            raise PipelineStepError(step.name, base.abort_reason)

        # 补齐步骤未显式设置的字段。
        if record.name != step.name:
            # 步骤可以重命名 record，但要打 warning 以便发现拼写错误。
            logger.warning(
                "pipeline step record name mismatch declared=%s record=%s",
                step.name,
                record.name,
            )
        if record.elapsed_ms is None:
            record.elapsed_ms = elapsed_ms
        if record.status is StepStatus.PENDING:
            record.status = StepStatus.SUCCEEDED

        base.add_step_record(record)
        logger.info(
            "pipeline step done step=%s status=%s elapsed_ms=%d total_tokens=%d",
            step.name,
            record.status.value,
            record.elapsed_ms,
            base.total_usage.total_tokens,
        )


def run_pipeline(steps: list[PipelineStep], ctx: object) -> object:
    """一次性执行流水线的便捷封装。"""
    return AgentPipeline(steps).run(ctx)
