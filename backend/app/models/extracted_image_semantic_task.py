"""抽取图片语义任务模型。

职责：
1. 描述单张抽取图片的语义分析任务。
2. 记录模型选择、提示词版本、结果与失败信息。

说明：
- 活动任务按 `extracted_image_id + target_model_key + overwrite_existing_snapshot`
  去重，避免同一图片重复提交并发语义分析。
- 任务完成后是否覆盖快照由 `overwrite_existing_snapshot` 与上层服务决定。
"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import BIGINT, Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, Text, text
from sqlmodel import Field, SQLModel

from .common import utc_now


class ExtractedImageSemanticTaskStatus(str, Enum):
    """抽取图片语义任务状态。"""

    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class ExtractedImageSemanticTaskBase(SQLModel):
    """抽取图片语义任务共享字段。"""

    extracted_image_id: int
    status: ExtractedImageSemanticTaskStatus = ExtractedImageSemanticTaskStatus.pending
    requested_model: str | None = None
    target_model: str | None = None
    target_model_key: str
    overwrite_existing_snapshot: bool = False
    result_model: str | None = None
    request_id: str | None = None
    prompt_path: str
    prompt_hash: str | None = None
    description: str | None = None
    error_message: str | None = None
    attempt_count: int = 0


class ExtractedImageSemanticTask(ExtractedImageSemanticTaskBase, table=True):
    """抽取图片语义任务持久化实体。

    该模型保留调用侧请求模型、实际执行模型和提示词指纹，便于结果追踪、
    去重复用以及问题排查。
    """

    __tablename__ = "extracted_image_semantic_tasks"

    __table_args__ = (
        Index(
            "ux_extracted_image_semantic_tasks_active",
            "extracted_image_id",
            "target_model_key",
            "overwrite_existing_snapshot",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    extracted_image_id: int = Field(
        sa_column=Column(BIGINT, ForeignKey("extracted_images.id"), nullable=False, index=True)
    )
    status: ExtractedImageSemanticTaskStatus = Field(
        default=ExtractedImageSemanticTaskStatus.pending,
        sa_column=Column(SAEnum(ExtractedImageSemanticTaskStatus, native_enum=False), nullable=False, index=True),
    )
    requested_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    target_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    target_model_key: str = Field(sa_column=Column(Text, nullable=False, index=True))
    overwrite_existing_snapshot: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, default=False, server_default=text("false")),
    )
    result_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    request_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    prompt_path: str = Field(sa_column=Column(Text, nullable=False))
    prompt_hash: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    attempt_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class ExtractedImageSemanticTaskRead(ExtractedImageSemanticTaskBase):
    """对外返回的抽取图片语义任务读取模型。"""

    id: UUID
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime
