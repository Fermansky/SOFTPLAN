"""文档实体模型。

职责：
1. 描述项目下文档的持久化结构与读写视图。
2. 维护文档与文件、软件之间的关联关系。

说明：
- `deleted_at` 用于软删除标记，不负责级联清理。
- `extra_info` 保存附加元数据，结构由上层业务决定。
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlmodel import Field, SQLModel

from .common import utc_now


class DocumentBase(SQLModel):
    """文档共享字段。

    供表模型与对外读写模型复用，保持基础字段语义一致。
    """

    file_id: UUID | None = None
    project_id: UUID
    software_id: UUID | None = None
    name: str
    description: str = ""
    extra_info: dict[str, Any] | None = None


class Document(DocumentBase, table=True):
    """文档持久化实体。

    每条记录归属于一个项目，可选关联原始文件与软件实体，并通过
    `deleted_at` 表示软删除状态。
    """

    __tablename__ = "documents"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    file_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("files.id"),
            nullable=True,
            index=True,
        ),
    )
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
    description: str = Field(default="", sa_column=Column(Text, nullable=False))
    extra_info: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    deleted_at: datetime | None = Field(default=None, nullable=True, index=True)


class DocumentCreate(DocumentBase):
    """创建文档时使用的输入模型。"""

    pass


class DocumentUpdate(SQLModel):
    """更新文档时使用的局部修改模型。"""

    file_id: UUID | None = None
    project_id: UUID | None = None
    software_id: UUID | None = None
    name: str | None = None
    description: str | None = None
    extra_info: dict[str, Any] | None = None


class DocumentRead(DocumentBase):
    """对外返回的文档读取模型。"""

    id: UUID
    created_at: datetime
    updated_at: datetime
