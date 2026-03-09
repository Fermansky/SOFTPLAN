from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import BIGINT, Column, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlmodel import Field, SQLModel

from .common import utc_now


class DocumentBase(SQLModel):
    project_id: UUID
    software_id: UUID | None = None
    name: str
    storage_bucket: str = "project-docs"
    storage_key: str
    file_size: int
    content_type: str
    extra_info: dict[str, Any] | None = None


class Document(DocumentBase, table=True):
    __tablename__ = "documents"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    project_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("projects.id"),
            nullable=False,
            index=True,
        )
    )
    software_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("softwares.id"),
            nullable=True,
            index=True,
        ),
    )
    name: str = Field(sa_column=Column(Text, nullable=False))
    storage_bucket: str = Field(default="project-docs", sa_column=Column(Text, nullable=False))
    storage_key: str = Field(sa_column=Column(Text, nullable=False))
    file_size: int = Field(sa_column=Column(BIGINT, nullable=False))
    content_type: str = Field(sa_column=Column(Text, nullable=False))
    extra_info: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    deleted_at: datetime | None = Field(default=None, nullable=True, index=True)


class DocumentCreate(DocumentBase):
    pass


class DocumentUpdate(SQLModel):
    project_id: UUID | None = None
    software_id: UUID | None = None
    name: str | None = None
    storage_bucket: str | None = None
    storage_key: str | None = None
    file_size: int | None = None
    content_type: str | None = None
    extra_info: dict[str, Any] | None = None


class DocumentRead(DocumentBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
