from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BIGINT, Column, Text
from sqlmodel import Field, SQLModel

from .common import utc_now


class FileRecordBase(SQLModel):
    file_hash: str = Field(sa_column=Column(Text, nullable=False, unique=True, index=True))
    storage_bucket: str = Field(sa_column=Column(Text, nullable=False))
    storage_key: str = Field(sa_column=Column(Text, nullable=False))
    file_size: int = Field(sa_column=Column(BIGINT, nullable=False))
    content_type: str = Field(default="application/octet-stream", sa_column=Column(Text, nullable=False))
    extension: str = Field(default="", sa_column=Column(Text, nullable=False))


class FileRecord(FileRecordBase, table=True):
    __tablename__ = "files"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)


class FileRecordCreate(FileRecordBase):
    pass


class FileRecordRead(FileRecordBase):
    id: UUID
    created_at: datetime
