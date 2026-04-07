from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlmodel import Field, SQLModel

from .common import utc_now


DEFAULT_DOCUMENT_PARSING_PDF_MODEL = "marker"
DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY = "__LLM_SERVICE_DEFAULT__"


class DocumentParsingTaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class DocumentParsingTaskBase(SQLModel):
    document_id: UUID
    file_id: UUID
    storage_bucket: str
    storage_key: str
    requested_pdf_model: str | None = None
    target_pdf_model: str = DEFAULT_DOCUMENT_PARSING_PDF_MODEL
    pdf_model_key: str = DEFAULT_DOCUMENT_PARSING_PDF_MODEL
    requested_image_model: str | None = None
    target_image_model: str | None = None
    image_model_key: str = DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY
    force_pdf_parse: bool = False
    pdf_result_source_task_id: UUID | None = None
    status: DocumentParsingTaskStatus = DocumentParsingTaskStatus.pending
    markdown: str | None = None
    image_hashes: dict[str, str] = Field(default_factory=dict)
    semantic_dispatches: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    attempt_count: int = 0


class DocumentParsingTask(DocumentParsingTaskBase, table=True):
    __tablename__ = "document_parsing_tasks"

    __table_args__ = (
        Index(
            "ux_document_parsing_tasks_document_active",
            "document_id",
            "pdf_model_key",
            "image_model_key",
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
    requested_pdf_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    target_pdf_model: str = Field(
        default=DEFAULT_DOCUMENT_PARSING_PDF_MODEL,
        sa_column=Column(Text, nullable=False, server_default=DEFAULT_DOCUMENT_PARSING_PDF_MODEL),
    )
    pdf_model_key: str = Field(
        default=DEFAULT_DOCUMENT_PARSING_PDF_MODEL,
        sa_column=Column(Text, nullable=False, server_default=DEFAULT_DOCUMENT_PARSING_PDF_MODEL),
    )
    requested_image_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    target_image_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    image_model_key: str = Field(
        default=DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY,
        sa_column=Column(Text, nullable=False, server_default=DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY),
    )
    force_pdf_parse: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("FALSE")),
    )
    pdf_result_source_task_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("document_parsing_tasks.id"),
            nullable=True,
            index=True,
        ),
    )
    status: DocumentParsingTaskStatus = Field(
        default=DocumentParsingTaskStatus.pending,
        sa_column=Column(SAEnum(DocumentParsingTaskStatus, native_enum=False), nullable=False, index=True),
    )
    markdown: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    image_hashes: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSONB, nullable=False))
    semantic_dispatches: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    attempt_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class DocumentParsingTaskCreate(SQLModel):
    document_id: UUID
    file_id: UUID
    storage_bucket: str
    storage_key: str
    requested_pdf_model: str | None = None
    target_pdf_model: str = DEFAULT_DOCUMENT_PARSING_PDF_MODEL
    pdf_model_key: str = DEFAULT_DOCUMENT_PARSING_PDF_MODEL
    requested_image_model: str | None = None
    target_image_model: str | None = None
    image_model_key: str = DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY
    force_pdf_parse: bool = False
    pdf_result_source_task_id: UUID | None = None


class DocumentParsingTaskRead(DocumentParsingTaskBase):
    id: UUID
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime
