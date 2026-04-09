"""软件实体模型。

职责：
1. 保存系统识别的软件基础信息。
2. 为项目与文档提供可复用的软件引用。

说明：
- `code` 是稳定唯一标识。
- `deleted_at` 用于软删除，不直接影响历史关联数据含义。
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel

from .common import utc_now


class SoftwareBase(SQLModel):
    """软件共享字段。"""

    code: str = Field(sa_column=Column(Text, nullable=False, unique=True, index=True))
    name: str = Field(sa_column=Column(Text, nullable=False))
    description: str = Field(default="", sa_column=Column(Text, nullable=False))


class Software(SoftwareBase, table=True):
    """软件持久化实体。"""

    __tablename__ = "softwares"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    deleted_at: datetime | None = Field(default=None, nullable=True, index=True)


class SoftwareCreate(SoftwareBase):
    """创建软件时使用的输入模型。"""

    pass


class SoftwareUpdate(SQLModel):
    """更新软件时使用的局部修改模型。"""

    code: str | None = None
    name: str | None = None
    description: str | None = None


class SoftwareRead(SoftwareBase):
    """对外返回的软件读取模型。"""

    id: UUID
    created_at: datetime
    updated_at: datetime
