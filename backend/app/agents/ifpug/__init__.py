"""IFPUG 流水线包导出。

仅暴露稳定的对外接口：
- 领域数据类型
- IfpugContext
- 流水线构造函数
- 已实现步骤的 agent / 错误类型（供路由层做 HTTP 状态码映射）
"""

from .context import IfpugContext
from .domain import (
    EXCLUDED_BY_DUPLICATE,
    EXCLUDED_BY_UNMAINTAINED,
    Attribute,
    DataEntity,
    EntityRelation,
    Exclusion,
    LogicalFile,
    SourceRef,
)
from .pipeline import build_logical_file_pipeline, list_registered_step_names
from .steps import (
    FilterUnmaintainedAgentError,
    FilterUnmaintainedAgentResult,
    FilterUnmaintainedStep,
    IdentifyEntitiesAgentError,
    IdentifyEntitiesAgentResult,
    IdentifyEntitiesStep,
    MergeDuplicatesAgentError,
    MergeDuplicatesAgentResult,
    MergeDuplicatesStep,
    run_filter_unmaintained_agent,
    run_identify_entities_agent,
    run_merge_duplicates_agent,
)
from .steps.s1_1_identify_entities import (
    IdentifyEntitiesPromptError,
    get_identify_entities_prompt_snapshot,
    load_identify_entities_prompt,
)
from .steps.s1_2_filter_unmaintained import (
    FilterUnmaintainedPromptError,
    get_filter_unmaintained_prompt_snapshot,
    load_filter_unmaintained_prompt,
)
from .steps.s1_3_merge_duplicates import (
    MergeDuplicatesPromptError,
    get_merge_duplicates_prompt_snapshot,
    load_merge_duplicates_prompt,
)

__all__ = [
    "Attribute",
    "DataEntity",
    "EXCLUDED_BY_DUPLICATE",
    "EXCLUDED_BY_UNMAINTAINED",
    "EntityRelation",
    "Exclusion",
    "FilterUnmaintainedAgentError",
    "FilterUnmaintainedAgentResult",
    "FilterUnmaintainedPromptError",
    "FilterUnmaintainedStep",
    "IdentifyEntitiesAgentError",
    "IdentifyEntitiesAgentResult",
    "IdentifyEntitiesPromptError",
    "IdentifyEntitiesStep",
    "IfpugContext",
    "LogicalFile",
    "MergeDuplicatesAgentError",
    "MergeDuplicatesAgentResult",
    "MergeDuplicatesPromptError",
    "MergeDuplicatesStep",
    "SourceRef",
    "build_logical_file_pipeline",
    "get_filter_unmaintained_prompt_snapshot",
    "get_identify_entities_prompt_snapshot",
    "get_merge_duplicates_prompt_snapshot",
    "list_registered_step_names",
    "load_filter_unmaintained_prompt",
    "load_identify_entities_prompt",
    "load_merge_duplicates_prompt",
    "run_filter_unmaintained_agent",
    "run_identify_entities_agent",
    "run_merge_duplicates_agent",
]
