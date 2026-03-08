from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class ProjectStatus(str, Enum):
    draft = "draft"
    analyzing = "analyzing"
    completed = "completed"
    archived = "archived"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectBase(SQLModel):
    name: str = Field(min_length=1, max_length=255, index=True)
    description: str = ""
    status: ProjectStatus = ProjectStatus.draft
    current_version_id: UUID | None = None


class Project(ProjectBase, table=True):
    __tablename__ = "projects"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)
    updated_at: datetime = Field(default_factory=utc_now, nullable=False)
    deleted_at: datetime | None = Field(default=None, nullable=True, index=True)


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: ProjectStatus | None = None
    current_version_id: UUID | None = None


class ProjectRead(ProjectBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
