"""项目模型。

职责：
1. 描述项目基础信息、状态与当前版本引用。
2. 为文档、软件关联等上层业务提供项目主实体。

说明：
- `deleted_at` 用于软删除标记。
- `current_version_id` 仅保存当前版本引用，不在模型层解释版本内容。
"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel

from .common import utc_now


class ProjectStatus(str, Enum):
    """项目状态枚举。"""

    draft = "draft"
    analyzing = "analyzing"
    completed = "completed"
    archived = "archived"


class ProjectBase(SQLModel):
    """项目共享字段。"""

    name: str = Field(min_length=1, max_length=255, index=True)
    description: str = ""
    status: ProjectStatus = ProjectStatus.draft
    current_version_id: UUID | None = None


class Project(ProjectBase, table=True):
    """项目持久化实体。"""

    __tablename__ = "projects"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    deleted_at: datetime | None = Field(default=None, nullable=True, index=True)


class ProjectCreate(ProjectBase):
    """创建项目时使用的输入模型。"""

    pass


class ProjectUpdate(SQLModel):
    """更新项目时使用的局部修改模型。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: ProjectStatus | None = None
    current_version_id: UUID | None = None


class ProjectRead(ProjectBase):
    """对外返回的项目读取模型。"""

    id: UUID
    created_at: datetime
    updated_at: datetime
