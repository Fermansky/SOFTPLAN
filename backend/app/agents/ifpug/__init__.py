"""IFPUG 流水线包导出。

仅暴露稳定的对外接口：
- 领域数据类型
- IfpugContext
- 流水线构造函数
- 已实现步骤的 agent / 错误类型（供路由层做 HTTP 状态码映射）
"""

from .context import IfpugContext
from .domain import (
    Attribute,
    DataEntity,
    EntityRelation,
    Exclusion,
    LogicalFile,
    SourceRef,
)
from .pipeline import build_logical_file_pipeline, list_registered_step_names
from .steps import (
    IdentifyEntitiesAgentError,
    IdentifyEntitiesAgentResult,
    IdentifyEntitiesStep,
    run_identify_entities_agent,
)
from .steps.s1_1_identify_entities import (
    IdentifyEntitiesPromptError,
    get_identify_entities_prompt_snapshot,
    load_identify_entities_prompt,
)

__all__ = [
    "Attribute",
    "DataEntity",
    "EntityRelation",
    "Exclusion",
    "IdentifyEntitiesAgentError",
    "IdentifyEntitiesAgentResult",
    "IdentifyEntitiesPromptError",
    "IdentifyEntitiesStep",
    "IfpugContext",
    "LogicalFile",
    "SourceRef",
    "build_logical_file_pipeline",
    "get_identify_entities_prompt_snapshot",
    "list_registered_step_names",
    "load_identify_entities_prompt",
    "run_identify_entities_agent",
]
