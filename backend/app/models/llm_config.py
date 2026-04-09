"""LLM 配置模型。

职责：
1. 描述可供系统选择的 LLM 提供商配置。
2. 区分持久化配置、创建更新输入与对外读取视图。

说明：
- `code` 是稳定的人类可读标识，用于服务间引用。
- `is_active` 用于标记当前激活配置，`enabled` 用于整体启停。
"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Column, DateTime, Float, Text
from sqlmodel import Field, SQLModel

from .common import utc_now


class LlmConfigProvider(str, Enum):
    """支持的 LLM 提供商类型。"""

    openai_compatible = "openai_compatible"


class LlmConfigBase(SQLModel):
    """LLM 配置共享字段。"""

    code: str = Field(min_length=1, max_length=100, index=True)
    name: str = Field(min_length=1, max_length=255)
    provider: LlmConfigProvider = LlmConfigProvider.openai_compatible
    base_url: str = Field(min_length=1, max_length=2048)
    default_model: str = Field(min_length=1, max_length=255)
    timeout_seconds: float = Field(default=30.0, gt=0)
    enabled: bool = True


class LlmConfig(LlmConfigBase, table=True):
    """LLM 配置持久化实体。

    包含实际可用的鉴权信息与启用状态，其中 `api_key` 仅在服务端内部使用，
    不应直接透出到读取模型。
    """

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
    """创建 LLM 配置时使用的输入模型。"""

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
    """更新 LLM 配置时使用的局部修改模型。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    provider: LlmConfigProvider | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: str | None = None
    default_model: str | None = Field(default=None, min_length=1, max_length=255)
    timeout_seconds: float | None = Field(default=None, gt=0)
    enabled: bool | None = None


class LlmConfigRead(SQLModel):
    """对外返回的 LLM 配置读取模型。

    该视图不暴露明文 `api_key`，只返回是否已配置以及脱敏结果。
    """

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
    """LLM 配置列表项视图。"""

    pass
