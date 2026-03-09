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
    return get_software_or_404(software_id, session)


@router.patch("/{software_id}", response_model=SoftwareRead)
def update_software(
    software_id: UUID, payload: SoftwareUpdate, session: Session = Depends(get_session)
) -> Software:
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
    software = get_software_or_404(software_id, session)
    now = utc_now()
    software.deleted_at = now
    software.updated_at = now
    session.add(software)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
