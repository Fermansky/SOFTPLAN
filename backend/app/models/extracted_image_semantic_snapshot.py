"""抽取图片语义快照模型。

职责：
1. 保存某张图片在指定模型键下的最新语义描述快照。
2. 记录快照与生成任务之间的来源关系，便于结果复用。

说明：
- 同一张图片在同一 `target_model_key` 下仅保留一条快照记录。
- `source_task_id` 仅描述快照来源，不代表任务当前状态。
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BIGINT, Column, DateTime, ForeignKey, Identity, Index, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field, SQLModel

from .common import utc_now


class ExtractedImageSemanticSnapshotBase(SQLModel):
    """抽取图片语义快照共享字段。"""

    extracted_image_id: int
    target_model_key: str
    result_model: str | None = None
    description: str
    source_task_id: UUID | None = None


class ExtractedImageSemanticSnapshot(ExtractedImageSemanticSnapshotBase, table=True):
    """抽取图片语义快照持久化实体。

    用于在语义任务完成后沉淀可复用结果，供后续流程直接命中快照。
    """

    __tablename__ = "extracted_image_semantic_snapshots"

    __table_args__ = (
        Index("ux_extracted_image_semantic_snapshots_image_model", "extracted_image_id", "target_model_key", unique=True),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BIGINT, Identity(always=True), primary_key=True, nullable=False),
    )
    extracted_image_id: int = Field(
        sa_column=Column(BIGINT, ForeignKey("extracted_images.id"), nullable=False, index=True)
    )
    target_model_key: str = Field(sa_column=Column(Text, nullable=False, index=True))
    result_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    description: str = Field(sa_column=Column(Text, nullable=False))
    source_task_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("extracted_image_semantic_tasks.id"),
            nullable=True,
            index=True,
        ),
    )
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class ExtractedImageSemanticSnapshotRead(ExtractedImageSemanticSnapshotBase):
    """对外返回的抽取图片语义快照读取模型。"""

    id: int
    created_at: datetime
    updated_at: datetime
