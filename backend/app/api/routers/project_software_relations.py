"""项目与软件关联路由。

职责：
1. 提供项目与软件关系的创建、查询、更新和删除接口。
2. 支持按项目或软件维度筛选关联记录。

说明：
- 关联键由 `project_id + software_id` 组成。
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..dependencies import (
    get_active_project_or_404,
    get_project_software_relation_or_404,
    get_software_or_404,
)
from ...database import get_session
from ...models import (
    ProjectSoftwareRelation,
    ProjectSoftwareRelationCreate,
    ProjectSoftwareRelationRead,
    ProjectSoftwareRelationUpdate,
)

router = APIRouter(prefix="/project-software-relations", tags=["project-software-relations"])


@router.post("", response_model=ProjectSoftwareRelationRead, status_code=status.HTTP_201_CREATED)
def create_project_software_relation(
    payload: ProjectSoftwareRelationCreate, session: Session = Depends(get_session)
) -> ProjectSoftwareRelation:
    """创建项目与软件关联。

    约束：
    - 项目和软件都必须存在且未软删除。

    副作用：
    - 写入关联记录并提交事务。
    """

    get_active_project_or_404(payload.project_id, session)
    get_software_or_404(payload.software_id, session)

    relation = ProjectSoftwareRelation(**payload.model_dump())
    session.add(relation)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Project software relation already exists",
        ) from exc
    session.refresh(relation)
    return relation


@router.get("", response_model=list[ProjectSoftwareRelationRead])
def list_project_software_relations(
    session: Session = Depends(get_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    project_id: UUID | None = None,
    software_id: UUID | None = None,
) -> list[ProjectSoftwareRelation]:
    """分页返回项目与软件关联列表，可按项目或软件筛选。"""

    statement = select(ProjectSoftwareRelation)
    if project_id is not None:
        statement = statement.where(ProjectSoftwareRelation.project_id == project_id)
    if software_id is not None:
        statement = statement.where(ProjectSoftwareRelation.software_id == software_id)
    statement = statement.order_by(ProjectSoftwareRelation.created_at.desc()).offset(offset).limit(limit)
    return list(session.exec(statement).all())


@router.get("/{project_id}/{software_id}", response_model=ProjectSoftwareRelationRead)
def get_project_software_relation(
    project_id: UUID, software_id: UUID, session: Session = Depends(get_session)
) -> ProjectSoftwareRelation:
    """返回单条项目与软件关联，未命中时返回 404。"""

    return get_project_software_relation_or_404(project_id, software_id, session)


@router.patch("/{project_id}/{software_id}", response_model=ProjectSoftwareRelationRead)
def update_project_software_relation(
    project_id: UUID,
    software_id: UUID,
    payload: ProjectSoftwareRelationUpdate,
    session: Session = Depends(get_session),
) -> ProjectSoftwareRelation:
    """更新项目与软件关联的可变字段并返回最新结果。"""

    relation = get_project_software_relation_or_404(project_id, software_id, session)
    update_data = payload.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(relation, field_name, value)

    session.add(relation)
    session.commit()
    session.refresh(relation)
    return relation


@router.delete("/{project_id}/{software_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_software_relation(
    project_id: UUID, software_id: UUID, session: Session = Depends(get_session)
) -> Response:
    """删除项目与软件关联记录。"""

    relation = get_project_software_relation_or_404(project_id, software_id, session)
    session.delete(relation)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
