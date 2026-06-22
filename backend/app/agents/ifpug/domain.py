"""IFPUG 流水线的领域数据模型。

本模块只放 IFPUG 专属的纯数据结构（dataclass），不依赖任何具体步骤或
LLM 调用细节，便于：
- 单测复用
- 序列化为 JSON 供调试端点回显
- 后续步骤之间通过明确的领域类型互相消费

设计要点：
- **不删除原则**：每一步过滤产生新的标签和理由（exclusion），原始候选
  实体始终保留在 ctx 中，便于"漏斗回放"和审计。
- **稳定 id**：实体 id 由代码（不是 LLM）分配后在整条流水线内保持不变。
- **rationale 是一等公民**：所有 LLM 决策都必须伴随理由，下游/UI 才能
  解释为什么实体被排除或被分类成 ILF/EIF。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 已知的排除标签集合。具体步骤可继续扩充，但建议统一在此约定语义。
EXCLUDED_BY_UNMAINTAINED = "excluded_by_unmaintained"          # 1.2 子任务
EXCLUDED_BY_CODE_DATA = "excluded_by_code_data"                # 1.4 子任务
EXCLUDED_BY_NOT_USER_REQUIRED = "excluded_by_not_user_required"  # 1.5 子任务
EXCLUDED_BY_ASSOCIATIVE = "excluded_by_associative"            # 1.6 子任务


@dataclass
class SourceRef:
    """实体在源文档中的引用位置（用于可追溯性）。

    `quote` 是原文摘录的简短片段；`location` 可以是结构化的位置描述
    （例如章节号、页码或段落索引），具体语义由抽取步骤决定。
    """

    quote: str
    location: str | None = None


@dataclass
class Attribute:
    """逻辑文件中的属性（DET 候选）。"""

    name: str
    description: str | None = None
    # 是否为"用户要求"的属性（1.5 子任务判定后回填）。
    # None 表示尚未判定。
    is_user_required: bool | None = None
    # 是否仅为外键（1.6 子任务判定关联实体时使用）。
    is_foreign_key: bool | None = None


@dataclass
class Exclusion:
    """对某个实体/逻辑文件做出的"排除"决策。

    一个对象可以被多次排除（来自不同步骤的多个理由都会被保留），最终
    "未被任何 Exclusion 命中"的对象才进入下一阶段。
    """

    tag: str
    rationale: str
    step: str


@dataclass
class DataEntity:
    """子任务 1.1 抽取出的候选数据实体。

    后续过滤类步骤通过往 `exclusions` 追加 Exclusion 来"打标"，而不是
    把实体从列表里删除。
    """

    id: str
    name: str
    description: str = ""
    attributes: list[Attribute] = field(default_factory=list)
    source_refs: list[SourceRef] = field(default_factory=list)
    # 抽取步骤可记录抽取时的辅助信息（例如初步分类提示），格式自由。
    extra: dict[str, Any] = field(default_factory=dict)
    exclusions: list[Exclusion] = field(default_factory=list)

    @property
    def is_excluded(self) -> bool:
        return bool(self.exclusions)


@dataclass
class EntityRelation:
    """实体之间的依赖关系（子任务 1.3 使用）。"""

    from_id: str
    to_id: str
    relation_type: str  # 'depends_on' | 'composed_of' | 'reference' 等
    rationale: str = ""


@dataclass
class LogicalFile:
    """合并后的逻辑文件（子任务 1.3 之后产生）。

    `entity_ids` 列出了组成该逻辑文件的所有 DataEntity id。
    `classification` 由子任务 1.7 填写。
    """

    id: str
    name: str
    entity_ids: list[str] = field(default_factory=list)
    classification: str | None = None  # 'ILF' | 'EIF' | None
    classification_rationale: str = ""
    exclusions: list[Exclusion] = field(default_factory=list)

    @property
    def is_excluded(self) -> bool:
        return bool(self.exclusions)
