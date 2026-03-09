from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel

from .common import utc_now


class SoftwareBase(SQLModel):
    code: str = Field(sa_column=Column(Text, nullable=False, unique=True, index=True))
    name: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(default="", sa_column=Column(Text, nullable=False))


class Software(SoftwareBase, table=True):
    __tablename__ = "softwares"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    deleted_at: datetime | None = Field(default=None, nullable=True, index=True)


class SoftwareCreate(SoftwareBase):
    pass


class SoftwareUpdate(SQLModel):
    code: str | None = None
    name: str | None = None
    description: str | None = None


class SoftwareRead(SoftwareBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
