from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import BIGINT, Column, DateTime, Enum as SAEnum, ForeignKey, Identity, Index, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field, SQLModel

from .common import utc_now


class DocumentParsingImageItemStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class DocumentParsingImageItemResultSource(str, Enum):
    semantic_snapshot = "semantic_snapshot"
    reused_semantic_task = "reused_semantic_task"
    submitted_semantic_task = "submitted_semantic_task"


class DocumentParsingImageItemBase(SQLModel):
    document_parsing_task_id: UUID
    source_key: str
    file_hash: str
    extracted_image_id: int
    semantic_task_id: UUID | None = None
    status: DocumentParsingImageItemStatus = DocumentParsingImageItemStatus.pending
    result_source: DocumentParsingImageItemResultSource | None = None
    error_message: str | None = None


class DocumentParsingImageItem(DocumentParsingImageItemBase, table=True):
    __tablename__ = "document_parsing_image_items"

    __table_args__ = (
        Index("ux_document_parsing_image_items_task_source_key", "document_parsing_task_id", "source_key", unique=True),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BIGINT, Identity(always=True), primary_key=True, nullable=False),
    )
    document_parsing_task_id: UUID = Field(
        sa_column=Column(PGUUID(as_uuid=True), ForeignKey("document_parsing_tasks.id"), nullable=False, index=True)
    )
    source_key: str = Field(sa_column=Column(Text, nullable=False))
    file_hash: str = Field(sa_column=Column(Text, nullable=False, index=True))
    extracted_image_id: int = Field(
        sa_column=Column(BIGINT, ForeignKey("extracted_images.id"), nullable=False, index=True)
    )
    semantic_task_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("extracted_image_semantic_tasks.id"),
            nullable=True,
            index=True,
        ),
    )
    status: DocumentParsingImageItemStatus = Field(
        default=DocumentParsingImageItemStatus.pending,
        sa_column=Column(SAEnum(DocumentParsingImageItemStatus, native_enum=False), nullable=False, index=True),
    )
    result_source: DocumentParsingImageItemResultSource | None = Field(
        default=None,
        sa_column=Column(SAEnum(DocumentParsingImageItemResultSource, native_enum=False), nullable=True),
    )
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class DocumentParsingImageItemRead(DocumentParsingImageItemBase):
    id: int
    created_at: datetime
    updated_at: datetime
