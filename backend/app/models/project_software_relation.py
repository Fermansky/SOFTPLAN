"""项目与软件关联模型。

职责：
1. 描述项目与软件之间的多对多关系。
2. 保存项目实际使用的软件版本信息。

说明：
- 关联以 `project_id + software_id` 组成复合主键。
- 删除项目或软件时依赖外键级联清理关联记录。
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field, SQLModel

from .common import utc_now


class ProjectSoftwareRelationBase(SQLModel):
    """项目与软件关联的共享字段。"""

    project_id: UUID
    software_id: UUID
    version: str | None = None


class ProjectSoftwareRelation(ProjectSoftwareRelationBase, table=True):
    """项目与软件关联持久化实体。"""

    __tablename__ = "project_software_relation"

    project_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    software_id: UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("softwares.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    version: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=utc_now, nullable=False)


class ProjectSoftwareRelationCreate(ProjectSoftwareRelationBase):
    """创建项目与软件关联时使用的输入模型。"""

    pass


class ProjectSoftwareRelationUpdate(SQLModel):
    """更新项目与软件关联时使用的局部修改模型。"""

    version: str | None = None


class ProjectSoftwareRelationRead(ProjectSoftwareRelationBase):
    """对外返回的项目与软件关联读取模型。"""

    created_at: datetime
