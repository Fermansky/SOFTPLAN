"""IFPUG 流水线各子任务的具体步骤实现。

每个子任务对应一个独立模块，模块内统一暴露：
- ``run_xxx_agent(...)``：薄薄的 LLM 调用函数（与现有 agent 风格一致）
- ``XxxStep``：把 ctx 接到 agent 调用的 ``PipelineStep`` 实现
- ``XxxAgentError`` / ``XxxPromptError``：本步骤的领域错误类型
"""

from .s1_1_identify_entities import (
    IdentifyEntitiesAgentError,
    IdentifyEntitiesAgentResult,
    IdentifyEntitiesStep,
    run_identify_entities_agent,
)
from .s1_2_filter_unmaintained import (
    FilterUnmaintainedAgentError,
    FilterUnmaintainedAgentResult,
    FilterUnmaintainedStep,
    run_filter_unmaintained_agent,
)
from .s1_3_merge_duplicates import (
    MergeDuplicatesAgentError,
    MergeDuplicatesAgentResult,
    MergeDuplicatesStep,
    run_merge_duplicates_agent,
)

__all__ = [
    "FilterUnmaintainedAgentError",
    "FilterUnmaintainedAgentResult",
    "FilterUnmaintainedStep",
    "IdentifyEntitiesAgentError",
    "IdentifyEntitiesAgentResult",
    "IdentifyEntitiesStep",
    "MergeDuplicatesAgentError",
    "MergeDuplicatesAgentResult",
    "MergeDuplicatesStep",
    "run_filter_unmaintained_agent",
    "run_identify_entities_agent",
    "run_merge_duplicates_agent",
]
