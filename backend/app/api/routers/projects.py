"""项目路由。

职责：
1. 提供项目的创建、列表、详情、更新与逻辑删除接口。
2. 对外屏蔽已软删除项目。

说明：
- 删除操作仅写入软删除时间，不物理删除记录。
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlmodel import Session, select

from ..dependencies import get_active_project_or_404
from ...database import get_session
from ...models import Project, ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)) -> Project:
    """创建项目并返回持久化结果。

    副作用：
    - 写入项目记录并提交事务。
    """

    project = Project(**payload.model_dump())
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(
    session: Session = Depends(get_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Project]:
    """分页返回未软删除项目列表。"""

    statement = (
        select(Project)
        .where(Project.deleted_at.is_(None))
        .order_by(Project.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(session.exec(statement).all())


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: UUID, session: Session = Depends(get_session)) -> Project:
    """返回单个未软删除项目详情，未命中时返回 404。"""

    return get_active_project_or_404(project_id, session)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    session: Session = Depends(get_session),
) -> Project:
    """更新项目的已提交字段并返回最新结果。

    副作用：
    - 更新项目字段与 `updated_at` 并提交事务。
    """

    project = get_active_project_or_404(project_id, session)

    update_data = payload.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(project, field_name, value)
    project.updated_at = datetime.now(timezone.utc)

    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: UUID, session: Session = Depends(get_session)) -> Response:
    """逻辑删除项目。

    副作用：
    - 设置 `deleted_at` 与 `updated_at` 并提交事务。
    """

    project = get_active_project_or_404(project_id, session)
    now = datetime.now(timezone.utc)
    project.deleted_at = now
    project.updated_at = now
    session.add(project)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
