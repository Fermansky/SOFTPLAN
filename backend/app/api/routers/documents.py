from uuid import UUID
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from minio.error import S3Error
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..dependencies import (
    get_active_document_or_404,
    get_active_project_or_404,
    get_file_or_404,
    get_minio_storage,
    get_software_or_404,
)
from ...database import get_session
from ...models import Document, DocumentCreate, DocumentRead, DocumentUpdate, FileRecord
from ...models.common import utc_now
from ...services import MinioStorage

router = APIRouter(prefix="/documents", tags=["documents"])


def _parse_extra_info(extra_info: str | None) -> dict[str, Any] | None:
    if extra_info is None or extra_info.strip() == "":
        return None
    try:
        parsed = json.loads(extra_info)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="extra_info is not valid JSON") from exc
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="extra_info must be a JSON object",
        )
    return parsed


@router.post("/upload", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    project_id: UUID = Form(...),
    software_id: UUID | None = Form(default=None),
    name: str | None = Form(default=None),
    description: str = Form(default=""),
    extra_info: str | None = Form(default=None),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    storage: MinioStorage = Depends(get_minio_storage),
) -> Document:
    get_active_project_or_404(project_id, session)
    if software_id is not None:
        get_software_or_404(software_id, session)

    file_content = await file.read()
    if not file_content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    content_type = file.content_type or "application/octet-stream"
    extension = Path(file.filename or "").suffix.lower()
    file_hash = hashlib.sha256(file_content).hexdigest()
    document_name = (name or "").strip() or file.filename or "uploaded-file"
    parsed_extra_info = _parse_extra_info(extra_info)

    try:
        storage_key = storage.upload_bytes(file_content, content_type=content_type, extension=extension)
    except S3Error as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO upload failed: {exc.code}",
        ) from exc

    file_record = FileRecord(
        file_hash=file_hash,
        storage_bucket=storage.bucket,
        storage_key=storage_key,
        file_size=len(file_content),
        content_type=content_type,
        extension=extension,
    )
    document = Document(
        file_id=file_record.id,
        project_id=project_id,
        software_id=software_id,
        name=document_name,
        description=description,
        extra_info=parsed_extra_info,
    )

    try:
        session.add(file_record)
        session.flush()
        document.file_id = file_record.id
        session.add(document)
        session.commit()
        session.refresh(document)
    except IntegrityError as exc:
        session.rollback()
        try:
            storage.remove_object(storage_key)
        except S3Error:
            pass
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="File hash already exists") from exc
    return document


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create_document(payload: DocumentCreate, session: Session = Depends(get_session)) -> Document:
    get_active_project_or_404(payload.project_id, session)
    if payload.software_id is not None:
        get_software_or_404(payload.software_id, session)
    if payload.file_id is not None:
        get_file_or_404(payload.file_id, session)

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
    if "file_id" in update_data and update_data["file_id"] is not None:
        get_file_or_404(update_data["file_id"], session)
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
