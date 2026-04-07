from datetime import datetime

from sqlalchemy import BIGINT, CHAR, Column, DateTime, Identity, Integer, Text, func
from sqlmodel import Field, SQLModel

from .common import utc_now


class ExtractedImageBase(SQLModel):
    file_hash: str = Field(min_length=64, max_length=64, sa_column=Column(CHAR(64), nullable=False, unique=True, index=True))
    storage_bucket: str = Field(sa_column=Column(Text, nullable=False))
    storage_key: str = Field(sa_column=Column(Text, nullable=False))
    file_size: int = Field(sa_column=Column(BIGINT, nullable=False))
    content_type: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    extension: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    width: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    height: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))


class ExtractedImageLegacySemanticSnapshot(SQLModel):
    semantic_description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    semantic_description_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    semantic_description_updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class ExtractedImage(ExtractedImageBase, table=True):
    __tablename__ = "extracted_images"

    id: int | None = Field(
        default=None,
        sa_column=Column(BIGINT, Identity(always=True), primary_key=True, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    semantic_description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    semantic_description_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    semantic_description_updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class ExtractedImageCreate(ExtractedImageBase):
    pass


class ExtractedImageUpdate(SQLModel):
    file_hash: str | None = Field(default=None, min_length=64, max_length=64)
    storage_bucket: str | None = None
    storage_key: str | None = None
    file_size: int | None = None
    content_type: str | None = None
    extension: str | None = None
    width: int | None = None
    height: int | None = None


class ExtractedImageRead(ExtractedImageBase, ExtractedImageLegacySemanticSnapshot):
    id: int
    created_at: datetime


