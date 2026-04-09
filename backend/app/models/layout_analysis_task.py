"""布局分析任务模型。

职责：
1. 描述文档布局分析任务的持久化结构与执行状态。
2. 保存布局分析产出的 markdown 和图片哈希摘要。

说明：
- 活动任务以 `document_id + layout_model_key` 去重，仅允许存在一条
  `pending` / `running` 记录。
- `layout_result_source_task_id` 用于标记结果复用来源，而不是父子任务关系。
"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlmodel import Field, SQLModel

from .common import utc_now


DEFAULT_LAYOUT_ANALYSIS_MODEL = "marker"
DEFAULT_DOCUMENT_PARSING_PDF_MODEL = DEFAULT_LAYOUT_ANALYSIS_MODEL


class LayoutAnalysisTaskStatus(str, Enum):
    """布局分析任务状态。"""

    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class LayoutAnalysisTaskBase(SQLModel):
    """布局分析任务共享字段。"""

    document_id: UUID
    file_id: UUID
    storage_bucket: str
    storage_key: str
    requested_layout_model: str | None = None
    target_layout_model: str = DEFAULT_LAYOUT_ANALYSIS_MODEL
    layout_model_key: str = DEFAULT_LAYOUT_ANALYSIS_MODEL
    force_layout_analysis: bool = False
    layout_result_source_task_id: UUID | None = None
    status: LayoutAnalysisTaskStatus = LayoutAnalysisTaskStatus.pending
    markdown: str | None = None
    image_hashes: dict[str, str] = Field(default_factory=dict)
    error_message: str | None = None
    attempt_count: int = 0


class LayoutAnalysisTask(LayoutAnalysisTaskBase, table=True):
    """布局分析任务持久化实体。

    该模型负责承接布局分析调度与结果落库。`started_at` 和 `finished_at`
    仅表示本次执行窗口，便于上层判断任务是否已完成。
    """

    __tablename__ = "layout_analysis_tasks"

    __table_args__ = (
        Index(
            "ux_layout_analysis_tasks_document_active",
            "document_id",
            "layout_model_key",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    document_id: UUID = Field(
        sa_column=Column(PGUUID(as_uuid=True), ForeignKey("documents.id"), nullable=False, index=True)
    )
    file_id: UUID = Field(sa_column=Column(PGUUID(as_uuid=True), ForeignKey("files.id"), nullable=False, index=True))
    storage_bucket: str = Field(sa_column=Column(Text, nullable=False))
    storage_key: str = Field(sa_column=Column(Text, nullable=False))
    requested_layout_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    target_layout_model: str = Field(
        default=DEFAULT_LAYOUT_ANALYSIS_MODEL,
        sa_column=Column(Text, nullable=False, server_default=DEFAULT_LAYOUT_ANALYSIS_MODEL),
    )
    layout_model_key: str = Field(
        default=DEFAULT_LAYOUT_ANALYSIS_MODEL,
        sa_column=Column(Text, nullable=False, server_default=DEFAULT_LAYOUT_ANALYSIS_MODEL),
    )
    force_layout_analysis: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("FALSE")),
    )
    layout_result_source_task_id: UUID | None = Field(
        default=None,
        sa_column=Column(PGUUID(as_uuid=True), ForeignKey("layout_analysis_tasks.id"), nullable=True, index=True),
    )
    status: LayoutAnalysisTaskStatus = Field(
        default=LayoutAnalysisTaskStatus.pending,
        sa_column=Column(SAEnum(LayoutAnalysisTaskStatus, native_enum=False), nullable=False, index=True),
    )
    markdown: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    image_hashes: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    attempt_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class LayoutAnalysisTaskCreate(SQLModel):
    """创建布局分析任务时使用的输入模型。"""

    document_id: UUID
    file_id: UUID
    storage_bucket: str
    storage_key: str
    requested_layout_model: str | None = None
    target_layout_model: str = DEFAULT_LAYOUT_ANALYSIS_MODEL
    layout_model_key: str = DEFAULT_LAYOUT_ANALYSIS_MODEL
    force_layout_analysis: bool = False
    layout_result_source_task_id: UUID | None = None


class LayoutAnalysisTaskRead(LayoutAnalysisTaskBase):
    """对外返回的布局分析任务读取模型。"""

    id: UUID
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime
