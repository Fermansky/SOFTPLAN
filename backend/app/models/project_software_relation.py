from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field, SQLModel

from .common import utc_now


class ProjectSoftwareRelationBase(SQLModel):
    project_id: UUID
    software_id: UUID
    version: str | None = None


class ProjectSoftwareRelation(ProjectSoftwareRelationBase, table=True):
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
    pass


class ProjectSoftwareRelationUpdate(SQLModel):
    version: str | None = None


class ProjectSoftwareRelationRead(ProjectSoftwareRelationBase):
    created_at: datetime
