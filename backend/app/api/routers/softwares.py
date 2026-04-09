"""软件路由。

职责：
1. 提供软件实体的创建、列表、详情、更新与逻辑删除接口。
2. 对外隐藏已软删除软件。

说明：
- 软件以 `code` 作为唯一业务标识。
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..dependencies import get_software_or_404
from ...database import get_session
from ...models import Software, SoftwareCreate, SoftwareRead, SoftwareUpdate
from ...models.common import utc_now

router = APIRouter(prefix="/softwares", tags=["softwares"])


@router.post("", response_model=SoftwareRead, status_code=status.HTTP_201_CREATED)
def create_software(payload: SoftwareCreate, session: Session = Depends(get_session)) -> Software:
    """创建软件记录。

    失败语义：
    - 当 `code` 冲突时返回 409。
    """

    software = Software(**payload.model_dump())
    session.add(software)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Software code already exists"
        ) from exc
    session.refresh(software)
    return software


@router.get("", response_model=list[SoftwareRead])
def list_softwares(
    session: Session = Depends(get_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Software]:
    """分页返回未软删除软件列表。"""

    statement = (
        select(Software)
        .where(Software.deleted_at.is_(None))
        .order_by(Software.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(session.exec(statement).all())


@router.get("/{software_id}", response_model=SoftwareRead)
def get_software(software_id: UUID, session: Session = Depends(get_session)) -> Software:
    """返回单个未软删除软件详情，未命中时返回 404。"""

    return get_software_or_404(software_id, session)


@router.patch("/{software_id}", response_model=SoftwareRead)
def update_software(
    software_id: UUID, payload: SoftwareUpdate, session: Session = Depends(get_session)
) -> Software:
    """更新软件的已提交字段。

    失败语义：
    - 当更新后的 `code` 冲突时返回 409。
    """

    software = get_software_or_404(software_id, session)
    update_data = payload.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(software, field_name, value)
    software.updated_at = utc_now()

    session.add(software)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Software code already exists"
        ) from exc
    session.refresh(software)
    return software


@router.delete("/{software_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_software(software_id: UUID, session: Session = Depends(get_session)) -> Response:
    """逻辑删除软件。

    副作用：
    - 设置 `deleted_at` 与 `updated_at` 并提交事务。
    """

    software = get_software_or_404(software_id, session)
    now = utc_now()
    software.deleted_at = now
    software.updated_at = now
    session.add(software)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
