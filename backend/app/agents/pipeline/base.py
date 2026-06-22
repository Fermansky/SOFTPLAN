"""步骤协议与流水线层的错误类型。

`PipelineStep` 表示一个不透明的工作单元：它接收一个 Context 对象
（可以是任何业务自定义的 dataclass），并被允许就地修改它。步骤通过
返回 `StepRecord` 来描述自己做了什么；流水线 runner 负责把这条
记录附加到 Context 对应的 `BasePipelineContext` 上，并决定是否继续
执行后续步骤。

步骤通过异常 / 哨兵返回值来表达控制流：
- 抛出 `PipelineAbort` 会干净地停止流水线（后续步骤不再执行）。
- 抛出其他任何异常都会被 runner 包装成 `PipelineStepError`，同时
  把 Context 标记为 aborted。
- 返回一个 `status == SKIPPED` 的 `StepRecord` 表示空操作（继续执行
  下一个步骤）。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .context import StepRecord


@runtime_checkable
class PipelineStep(Protocol):
    """流水线步骤的最小契约。

    实现通常是一个薄类，具备 `name` 属性以及 `run(ctx) -> StepRecord`
    方法。我们采用鸭子类型而非基类继承，让业务代码可以保持无继承的
    扁平风格。
    """

    name: str

    def run(self, ctx: object) -> StepRecord:  # pragma: no cover - protocol
        ...


class PipelineAbort(Exception):
    """由步骤抛出，用于在不视为失败的情况下停止流水线。

    适用于步骤判断后续工作已无意义的场景（例如过滤后候选集为空），
    此时已积累的部分 Context 仍然有效。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PipelineStepError(RuntimeError):
    """当某个步骤抛出未预期的异常时由 runner 抛出。

    包装原始异常，并携带出错步骤的名字，便于 API 层映射到对应的 HTTP
    响应。
    """

    def __init__(self, step_name: str, message: str) -> None:
        super().__init__(f"[{step_name}] {message}")
        self.step_name = step_name
        self.message = message
