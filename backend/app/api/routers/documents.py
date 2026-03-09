from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlmodel import Session, select

from ..dependencies import get_active_document_or_404, get_active_project_or_404, get_software_or_404
from ...database import get_session
from ...models import Document, DocumentCreate, DocumentRead, DocumentUpdate
from ...models.common import utc_now

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create_document(payload: DocumentCreate, session: Session = Depends(get_session)) -> Document:
    get_active_project_or_404(payload.project_id, session)
    if payload.software_id is not None:
        get_software_or_404(payload.software_id, session)

    document = Document(**payload.model_dump())
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


@router.get("", response_model=list[DocumentRead])
def list_documents(
    session: Session = Depends(get_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    project_id: UUID | None = None,
    software_id: UUID | None = None,
) -> list[Document]:
    statement = select(Document).where(Document.deleted_at.is_(None))
    if project_id is not None:
        statement = statement.where(Document.project_id == project_id)
    if software_id is not None:
        statement = statement.where(Document.software_id == software_id)
    statement = statement.order_by(Document.created_at.desc()).offset(offset).limit(limit)
    return list(session.exec(statement).all())


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(document_id: UUID, session: Session = Depends(get_session)) -> Document:
    return get_active_document_or_404(document_id, session)


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: UUID, payload: DocumentUpdate, session: Session = Depends(get_session)
) -> Document:
    document = get_active_document_or_404(document_id, session)

    update_data = payload.model_dump(exclude_unset=True)
    if "project_id" in update_data and update_data["project_id"] is not None:
        get_active_project_or_404(update_data["project_id"], session)
    if "software_id" in update_data and update_data["software_id"] is not None:
        get_software_or_404(update_data["software_id"], session)

    for field_name, value in update_data.items():
        setattr(document, field_name, value)
    document.updated_at = utc_now()

    session.add(document)
    session.commit()
    session.refresh(document)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: UUID, session: Session = Depends(get_session)) -> Response:
    document = get_active_document_or_404(document_id, session)
    now = utc_now()
    document.deleted_at = now
    document.updated_at = now
    session.add(document)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

