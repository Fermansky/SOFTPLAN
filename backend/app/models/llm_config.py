from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Float, Text
from sqlmodel import Field, SQLModel

from .common import utc_now


class LlmConfigProvider(str, Enum):
    openai_compatible = "openai_compatible"


class LlmConfigBase(SQLModel):
    code: str = Field(min_length=1, max_length=100, index=True)
    name: str = Field(min_length=1, max_length=255)
    provider: LlmConfigProvider = LlmConfigProvider.openai_compatible
    base_url: str = Field(min_length=1, max_length=2048)
    default_model: str = Field(min_length=1, max_length=255)
    timeout_seconds: float = Field(default=30.0, gt=0)
    enabled: bool = True


class LlmConfig(LlmConfigBase, table=True):
    __tablename__ = "llm_configs"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    code: str = Field(sa_column=Column(Text, nullable=False, unique=True, index=True))
    name: str = Field(sa_column=Column(Text, nullable=False))
    provider: LlmConfigProvider = Field(
        default=LlmConfigProvider.openai_compatible,
        sa_column=Column(Text, nullable=False),
    )
    base_url: str = Field(sa_column=Column(Text, nullable=False))
    api_key: str = Field(sa_column=Column(Text, nullable=False))
    default_model: str = Field(sa_column=Column(Text, nullable=False))
    timeout_seconds: float = Field(default=30.0, sa_column=Column(Float, nullable=False, default=30.0))
    is_active: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, default=False, index=True))
    enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, default=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    deleted_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True, index=True))


class LlmConfigCreate(SQLModel):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    provider: LlmConfigProvider = LlmConfigProvider.openai_compatible
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str
    default_model: str = Field(min_length=1, max_length=255)
    timeout_seconds: float = Field(default=30.0, gt=0)
    enabled: bool = True
    is_active: bool = False


class LlmConfigUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    provider: LlmConfigProvider | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: str | None = None
    default_model: str | None = Field(default=None, min_length=1, max_length=255)
    timeout_seconds: float | None = Field(default=None, gt=0)
    enabled: bool | None = None


class LlmConfigRead(SQLModel):
    id: UUID
    code: str
    name: str
    provider: LlmConfigProvider
    base_url: str
    default_model: str
    timeout_seconds: float
    is_active: bool
    enabled: bool
    has_api_key: bool
    api_key_masked: str | None
    created_at: datetime
    updated_at: datetime


class LlmConfigListItem(LlmConfigRead):
    pass
