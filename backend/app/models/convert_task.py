from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlmodel import Field, SQLModel

from .common import utc_now


class ConvertTaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class ConvertTaskBase(SQLModel):
    document_id: UUID
    file_id: UUID
    storage_bucket: str
    storage_key: str
    status: ConvertTaskStatus = ConvertTaskStatus.pending
    markdown: str | None = None
    image_hashes: dict[str, str] = Field(default_factory=dict)
    error_message: str | None = None
    attempt_count: int = 0


class ConvertTask(ConvertTaskBase, table=True):
    __tablename__ = "convert_tasks"

    __table_args__ = (
        Index(
            "ux_convert_tasks_document_active",
            "document_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    document_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("documents.id"),
            nullable=False,
            index=True,
        )
    )
    file_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("files.id"),
            nullable=False,
            index=True,
        )
    )
    storage_bucket: str = Field(sa_column=Column(Text, nullable=False))
    storage_key: str = Field(sa_column=Column(Text, nullable=False))
    status: ConvertTaskStatus = Field(
        default=ConvertTaskStatus.pending,
        sa_column=Column(SAEnum(ConvertTaskStatus, native_enum=False), nullable=False, index=True),
    )
    markdown: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    image_hashes: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    attempt_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class ConvertTaskCreate(SQLModel):
    document_id: UUID
    file_id: UUID
    storage_bucket: str
    storage_key: str


class ConvertTaskRead(ConvertTaskBase):
    id: UUID
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


