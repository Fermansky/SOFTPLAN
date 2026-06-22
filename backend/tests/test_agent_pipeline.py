"""通用 Agent Pipeline 框架的单元测试（PR1）。

这些测试有意不依赖任何 LLM 服务。所有 Step 都是手写的桩对象，从而
可以在隔离环境下验证执行顺序、abort 语义、错误包装以及 usage 累加
等行为。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest import TestCase

from backend.app.agents.pipeline import (
    AgentPipeline,
    BasePipelineContext,
    PipelineAbort,
    PipelineStepError,
    StepRecord,
    StepStatus,
    run_pipeline,
    sum_usage,
    zero_usage,
)
from backend.app.services import LlmUsage


@dataclass
class _DemoCtx:
    """模拟业务 Context：通过组合方式持有 BasePipelineContext。"""

    base: BasePipelineContext = field(default_factory=BasePipelineContext)
    trail: list[str] = field(default_factory=list)
    payload: dict[str, str] = field(default_factory=dict)


class _AppendStep:
    """把自身名字写入 ctx.trail，并返回一个成功的 StepRecord。"""

    def __init__(self, name: str, *, usage: LlmUsage | None = None, metrics: dict | None = None) -> None:
        self.name = name
        self._usage = usage or zero_usage()
        self._metrics = metrics or {}

    def run(self, ctx: _DemoCtx) -> StepRecord:
        ctx.trail.append(self.name)
        return StepRecord(name=self.name, usage=self._usage, metrics=dict(self._metrics))


class _AbortStep:
    name = "abort_step"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def run(self, ctx: _DemoCtx) -> StepRecord:
        ctx.trail.append(self.name)
        raise PipelineAbort(self._reason)


class _BoomStep:
    name = "boom_step"

    def run(self, ctx: _DemoCtx) -> StepRecord:
        ctx.trail.append(self.name)
        raise ValueError("kaboom")


class _BadReturnStep:
    name = "bad_return_step"

    def run(self, ctx: _DemoCtx):  # type: ignore[override]
        return "not a step record"


class UsageHelpersTests(TestCase):
    def test_zero_usage_is_all_zero(self) -> None:
        u = zero_usage()
        self.assertEqual((u.prompt_tokens, u.completion_tokens, u.total_tokens), (0, 0, 0))

    def test_sum_usage_field_wise(self) -> None:
        a = LlmUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3)
        b = LlmUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        self.assertEqual(
            sum_usage(a, b),
            LlmUsage(prompt_tokens=11, completion_tokens=22, total_tokens=33),
        )


class PipelineHappyPathTests(TestCase):
    def test_steps_run_in_order_and_record_status(self) -> None:
        ctx = _DemoCtx()
        pipeline = AgentPipeline([_AppendStep("a"), _AppendStep("b"), _AppendStep("c")])

        pipeline.run(ctx)

        self.assertEqual(ctx.trail, ["a", "b", "c"])
        self.assertEqual([r.name for r in ctx.base.step_records], ["a", "b", "c"])
        for record in ctx.base.step_records:
            self.assertEqual(record.status, StepStatus.SUCCEEDED)
            self.assertIsNotNone(record.elapsed_ms)
        self.assertFalse(ctx.base.aborted)

    def test_total_usage_accumulates_across_steps(self) -> None:
        ctx = _DemoCtx()
        pipeline = AgentPipeline(
            [
                _AppendStep("a", usage=LlmUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)),
                _AppendStep("b", usage=LlmUsage(prompt_tokens=2, completion_tokens=3, total_tokens=5)),
            ]
        )

        pipeline.run(ctx)

        self.assertEqual(
            ctx.base.total_usage,
            LlmUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20),
        )

    def test_run_pipeline_helper_executes_all_steps(self) -> None:
        ctx = _DemoCtx()
        run_pipeline([_AppendStep("only")], ctx)
        self.assertEqual(ctx.trail, ["only"])
        self.assertEqual(ctx.base.step_records[0].status, StepStatus.SUCCEEDED)


class PipelineAbortTests(TestCase):
    def test_abort_short_circuits_subsequent_steps(self) -> None:
        ctx = _DemoCtx()
        pipeline = AgentPipeline(
            [_AppendStep("a"), _AbortStep("nothing-to-do"), _AppendStep("c")]
        )

        pipeline.run(ctx)

        self.assertEqual(ctx.trail, ["a", "abort_step"])
        self.assertTrue(ctx.base.aborted)
        self.assertEqual(ctx.base.abort_reason, "nothing-to-do")
        self.assertEqual(ctx.base.aborted_step, "abort_step")

        statuses = [(r.name, r.status) for r in ctx.base.step_records]
        self.assertEqual(
            statuses,
            [("a", StepStatus.SUCCEEDED), ("abort_step", StepStatus.SKIPPED), ("c", StepStatus.SKIPPED)],
        )


class PipelineErrorTests(TestCase):
    def test_unexpected_exception_wrapped_in_step_error(self) -> None:
        ctx = _DemoCtx()
        pipeline = AgentPipeline([_AppendStep("a"), _BoomStep(), _AppendStep("c")])

        with self.assertRaises(PipelineStepError) as cm:
            pipeline.run(ctx)

        self.assertEqual(cm.exception.step_name, "boom_step")
        self.assertIn("kaboom", str(cm.exception))
        self.assertTrue(ctx.base.aborted)
        self.assertEqual(ctx.base.aborted_step, "boom_step")
        # 'a' 成功，'boom_step' 失败；'c' 不应被执行。
        self.assertEqual(ctx.trail, ["a", "boom_step"])
        self.assertEqual(
            [(r.name, r.status) for r in ctx.base.step_records],
            [("a", StepStatus.SUCCEEDED), ("boom_step", StepStatus.FAILED)],
        )

    def test_step_returning_non_record_is_rejected(self) -> None:
        ctx = _DemoCtx()
        pipeline = AgentPipeline([_BadReturnStep()])

        with self.assertRaises(PipelineStepError) as cm:
            pipeline.run(ctx)

        self.assertEqual(cm.exception.step_name, "bad_return_step")
        self.assertTrue(ctx.base.aborted)


class PipelineConstructionTests(TestCase):
    def test_empty_steps_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AgentPipeline([])

    def test_context_must_expose_base(self) -> None:
        @dataclass
        class _BadCtx:
            value: int = 0

        pipeline = AgentPipeline([_AppendStep("a")])
        with self.assertRaises(TypeError):
            pipeline.run(_BadCtx())

    def test_base_pipeline_context_used_directly(self) -> None:
        # 当 ctx 本身就是 BasePipelineContext 时，无需自定义 extractor。
        ctx = BasePipelineContext()

        class _Step:
            name = "noop"

            def run(self, ctx: object) -> StepRecord:
                return StepRecord(name="noop")

        AgentPipeline([_Step()]).run(ctx)
        self.assertEqual(len(ctx.step_records), 1)
        self.assertEqual(ctx.step_records[0].status, StepStatus.SUCCEEDED)

    def test_custom_base_extractor(self) -> None:
        @dataclass
        class _NestedCtx:
            meta: BasePipelineContext = field(default_factory=BasePipelineContext)

        ctx = _NestedCtx()
        pipeline = AgentPipeline([_AppendStep("a")], base_extractor=lambda c: c.meta)
        # _AppendStep 会修改 ctx.trail，但 _NestedCtx 默认没有该字段，临时补一个。
        ctx.trail = []  # type: ignore[attr-defined]
        pipeline.run(ctx)
        self.assertEqual(len(ctx.meta.step_records), 1)
