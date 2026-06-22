"""IFPUG 流水线运行期共享的 Context。

`IfpugContext` 通过组合方式持有 `BasePipelineContext`，从而复用通用
框架提供的 step 记录与 usage 累加能力，同时保留 IFPUG 自己的领域字段
（候选实体、关系、逻辑文件等）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlmodel import Session

from ..pipeline import BasePipelineContext
from .domain import DataEntity, EntityRelation, LogicalFile


@dataclass
class IfpugContext:
    """贯穿 IFPUG 逻辑文件识别流水线的共享上下文。

    字段分四类：
    1. **入参**（构造后约定不再修改）：原始文档、计数范围、用户需求、
       Session 与 LLM 配置。
    2. **累积产物**：每一步把自己的产出写到对应字段；过滤步骤只追加
       Exclusion，不删除元素。
    3. **运行元信息**：通过 `base` 字段委托给 `BasePipelineContext`。
    4. **流水线元信息**：`source_label` 等便于审计的辅助字段。
    """

    # ---- 入参 ----
    source_document: str
    counting_scope: str = ""
    user_requirements: str = ""
    session: Session | None = None
    config_id: UUID | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    # ---- 累积产物 ----
    candidate_entities: list[DataEntity] = field(default_factory=list)
    relations: list[EntityRelation] = field(default_factory=list)
    logical_files: list[LogicalFile] = field(default_factory=list)

    # ---- 流水线通用元信息（组合复用）----
    base: BasePipelineContext = field(default_factory=BasePipelineContext)

    # ---- 辅助 ----
    source_label: str | None = None  # 文档来源标识，仅用于日志/审计

    # 用于在多个步骤间稳定生成新的 entity / logical_file id。
    _next_entity_seq: int = 0
    _next_logical_file_seq: int = 0

    def next_entity_id(self) -> str:
        self._next_entity_seq += 1
        return f"E{self._next_entity_seq:03d}"

    def next_logical_file_id(self) -> str:
        self._next_logical_file_seq += 1
        return f"LF{self._next_logical_file_seq:03d}"

    def active_entities(self) -> list[DataEntity]:
        """返回当前未被任何步骤排除的候选实体。"""
        return [entity for entity in self.candidate_entities if not entity.is_excluded]

    def active_logical_files(self) -> list[LogicalFile]:
        """返回当前未被任何步骤排除的逻辑文件。"""
        return [lf for lf in self.logical_files if not lf.is_excluded]
