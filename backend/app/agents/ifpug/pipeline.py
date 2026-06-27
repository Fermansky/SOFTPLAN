"""IFPUG 逻辑文件识别流水线的组装入口。

该模块负责把当前已实现的 IFPUG 步骤组合成 `AgentPipeline` 实例，
并提供按"跑到第几步"截断的便捷构造函数（PR2 阶段仅有 s1_1）。

调用方典型用法：
```python
pipeline = build_logical_file_pipeline()
pipeline.run(ifpug_context)
```
"""

from __future__ import annotations

from ..pipeline import AgentPipeline, PipelineStep
from .steps import (
    FilterAssociativeStep,
    FilterCodeDataStep,
    FilterNotUserRequiredStep,
    FilterUnmaintainedStep,
    IdentifyEntitiesStep,
    MergeDuplicatesStep,
)

# 已实现的子任务按执行顺序登记。后续 PR 增加新步骤时，按子任务编号
# 顺序追加到此列表（``s1_7`` ...），不需要改下游调用。
_REGISTERED_STEPS: list[tuple[str, type[PipelineStep]]] = [
    ("s1_1", IdentifyEntitiesStep),
    ("s1_2", FilterUnmaintainedStep),
    ("s1_3", MergeDuplicatesStep),
    ("s1_4", FilterCodeDataStep),
    ("s1_5", FilterNotUserRequiredStep),
    ("s1_6", FilterAssociativeStep),
]


def _build_steps(*, until: str | None) -> list[PipelineStep]:
    steps: list[PipelineStep] = []
    for short_name, cls in _REGISTERED_STEPS:
        steps.append(cls())
        if until is not None and short_name == until:
            return steps
    if until is not None:
        raise ValueError(f"unknown ifpug step short name: {until}")
    return steps


def build_logical_file_pipeline(*, until: str | None = None) -> AgentPipeline:
    """构建 IFPUG 逻辑文件识别流水线。

    Args:
        until: 可选的"截止步骤短名"（例如 ``"s1_1"``）。提供时仅包含
            该步骤及其之前的步骤，便于调试。``None`` 表示包含所有已
            注册步骤。

    Returns:
        组装好的 `AgentPipeline`，可通过 `IfpugContext` 实例直接 `run`。
    """
    steps = _build_steps(until=until)
    return AgentPipeline(steps, base_extractor=lambda ctx: ctx.base)


def list_registered_step_names() -> list[str]:
    """按执行顺序返回当前已注册的步骤短名。"""
    return [name for name, _ in _REGISTERED_STEPS]
