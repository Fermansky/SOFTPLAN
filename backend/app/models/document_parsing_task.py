"""文档解析任务模型。

职责：
1. 描述文档解析任务的持久化结构、状态与结果摘要。
2. 关联布局分析任务，并记录图片语义处理的统计信息。

说明：
- 活动任务以 `document_id + layout_model_key + image_model_key` 去重，
  仅允许 `pending` / `running` 状态各保留一条活动记录。
- `requested_*` 保留调用方原始请求，`target_*` 与 `*_model_key`
  表示实际执行与去重使用的模型标识。
"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlmodel import Field, SQLModel

from .common import utc_now
from .extracted_image_semantic_task import ACTIVE_LLM_CONFIG_KEY
from .layout_analysis_task import DEFAULT_LAYOUT_ANALYSIS_MODEL


DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY = "__LLM_SERVICE_DEFAULT__"


class DocumentParsingTaskStatus(str, Enum):
    """文档解析任务状态。

    表示任务从等待处理到执行结束的生命周期。
    """

    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class DocumentParsingTaskBase(SQLModel):
    """文档解析任务共享字段。

    包含任务输入、模型选择、结果汇总和错误信息，供表模型与读取模型复用。
    """

    document_id: UUID
    file_id: UUID
    storage_bucket: str
    storage_key: str
    requested_layout_model: str | None = None
    target_layout_model: str = DEFAULT_LAYOUT_ANALYSIS_MODEL
    layout_model_key: str = DEFAULT_LAYOUT_ANALYSIS_MODEL
    requested_image_model: str | None = None
    target_image_model: str | None = None
    image_model_key: str = DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY
    image_llm_config_id: UUID | None = None
    image_llm_config_code: str | None = None
    image_llm_config_key: str = ACTIVE_LLM_CONFIG_KEY
    force_layout_analysis: bool = False
    force_image_semantic_recognition: bool = False
    layout_task_id: UUID
    status: DocumentParsingTaskStatus = DocumentParsingTaskStatus.pending
    markdown: str | None = None
    image_hashes: dict[str, str] = Field(default_factory=dict)
    image_total_count: int = 0
    image_succeeded_count: int = 0
    image_failed_count: int = 0
    error_message: str | None = None
    attempt_count: int = 0


class DocumentParsingTask(DocumentParsingTaskBase, table=True):
    """文档解析任务持久化实体。

    该表负责串联文档、布局分析任务和图片语义处理结果。`started_at`
    与 `finished_at` 仅描述执行窗口，不额外表达调度重试策略。
    """

    __tablename__ = "document_parsing_tasks"

    __table_args__ = (
        Index(
            "ux_document_parsing_tasks_document_active",
            "document_id",
            "layout_model_key",
            "image_model_key",
            "image_llm_config_key",
            "force_image_semantic_recognition",
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
    requested_image_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    target_image_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    image_model_key: str = Field(
        default=DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY,
        sa_column=Column(Text, nullable=False, server_default=DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY),
    )
    image_llm_config_id: UUID | None = Field(default=None, nullable=True, index=True)
    image_llm_config_code: str | None = Field(default=None, sa_column=Column(Text, nullable=True, index=True))
    image_llm_config_key: str = Field(
        default=ACTIVE_LLM_CONFIG_KEY,
        sa_column=Column(Text, nullable=False, server_default=ACTIVE_LLM_CONFIG_KEY),
    )
    force_layout_analysis: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("FALSE")),
    )
    force_image_semantic_recognition: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("FALSE")),
    )
    layout_task_id: UUID = Field(
        sa_column=Column(PGUUID(as_uuid=True), ForeignKey("layout_analysis_tasks.id"), nullable=False, index=True)
    )
    status: DocumentParsingTaskStatus = Field(
        default=DocumentParsingTaskStatus.pending,
        sa_column=Column(SAEnum(DocumentParsingTaskStatus, native_enum=False), nullable=False, index=True),
    )
    markdown: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    image_hashes: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    image_total_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    image_succeeded_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    image_failed_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    attempt_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class DocumentParsingTaskCreate(SQLModel):
    """创建文档解析任务时使用的输入模型。

    仅包含提交任务所需的静态信息，不包含运行态统计字段。
    """

    document_id: UUID
    file_id: UUID
    storage_bucket: str
    storage_key: str
    requested_layout_model: str | None = None
    target_layout_model: str = DEFAULT_LAYOUT_ANALYSIS_MODEL
    layout_model_key: str = DEFAULT_LAYOUT_ANALYSIS_MODEL
    requested_image_model: str | None = None
    target_image_model: str | None = None
    image_model_key: str = DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY
    image_llm_config_id: UUID | None = None
    image_llm_config_code: str | None = None
    image_llm_config_key: str = ACTIVE_LLM_CONFIG_KEY
    force_layout_analysis: bool = False
    force_image_semantic_recognition: bool = False
    layout_task_id: UUID


class DocumentParsingTaskRead(DocumentParsingTaskBase):
    """对外返回的文档解析任务读取模型。"""

    id: UUID
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime
