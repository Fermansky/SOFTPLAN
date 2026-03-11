import logging
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

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
logger = logging.getLogger(__name__)


def _parse_extra_info(extra_info: str | None) -> dict[str, Any] | None:
    if extra_info is None or extra_info.strip() == "":
        return None
    try:
        parsed = json.loads(extra_info)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid extra_info JSON payload")
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="extra_info is not valid JSON") from exc
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        logger.warning("extra_info is not a JSON object")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="extra_info must be a JSON object",
        )
    return parsed


def _find_file_by_hash(file_hash: str, session: Session) -> FileRecord | None:
    statement = select(FileRecord).where(FileRecord.file_hash == file_hash)
    return session.exec(statement).first()


def _build_download_filename(name: str, extension: str) -> str:
    filename = name.strip() or "document"
    normalized_extension = extension.strip()
    if normalized_extension and not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"
    if normalized_extension and not filename.lower().endswith(normalized_extension.lower()):
        filename = f"{filename}{normalized_extension}"
    return filename


def _build_content_disposition(filename: str) -> str:
    ascii_filename = filename.encode("ascii", "ignore").decode("ascii").strip() or "document"
    encoded_filename = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{encoded_filename}"


def _build_documents_query(
    *,
    offset: int,
    limit: int,
    project_id: UUID | None = None,
    software_id: UUID | None = None,
):
    statement = select(Document).where(Document.deleted_at.is_(None))
    if project_id is not None:
        statement = statement.where(Document.project_id == project_id)
    if software_id is not None:
        statement = statement.where(Document.software_id == software_id)
    return statement.order_by(Document.created_at.desc()).offset(offset).limit(limit)


def _cleanup_uploaded_object(storage: MinioStorage, storage_key: str) -> None:
    try:
        storage.remove_object(storage_key)
    except S3Error:
        logger.warning("Failed to cleanup uploaded object from MinIO, storage_key=%s", storage_key)


def _repair_missing_object_if_needed(
    *,
    file_record: FileRecord,
    storage: MinioStorage,
    file_content: bytes,
    content_type: str,
    extension: str,
) -> str | None:
    if storage.object_exists(file_record.storage_key):
        return None

    # Existing hash record points to a missing object. Re-upload and update
    # metadata so future dedupe can safely reuse this file row.
    logger.warning(
        "Existing file object missing, repairing file_id=%s, old_storage_key=%s",
        file_record.id,
        file_record.storage_key,
    )
    new_storage_key = storage.upload_bytes(file_content, content_type=content_type, extension=extension)
    file_record.storage_bucket = storage.bucket
    file_record.storage_key = new_storage_key
    file_record.file_size = len(file_content)
    file_record.content_type = content_type
    file_record.extension = extension
    return new_storage_key


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
    logger.info("Uploading document for project_id=%s, software_id=%s", project_id, software_id)
    get_active_project_or_404(project_id, session)
    if software_id is not None:
        get_software_or_404(software_id, session)

    file_content = await file.read()
    if not file_content:
        logger.warning("Rejected empty uploaded file for project_id=%s", project_id)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    content_type = file.content_type or "application/octet-stream"
    extension = Path(file.filename or "").suffix.lower()
    file_hash = hashlib.sha256(file_content).hexdigest()
    document_name = (name or "").strip() or file.filename or "uploaded-file"
    parsed_extra_info = _parse_extra_info(extra_info)

    # Dedupe by content hash. If an identical physical file already exists,
    # only create a new document row referencing the existing file_id.
    file_record = _find_file_by_hash(file_hash, session)
    storage_key: str | None = None
    uploaded_storage_key: str | None = None
    created_new_file = False
    repaired_existing_file = False
    if file_record is None:
        try:
            storage_key = storage.upload_bytes(file_content, content_type=content_type, extension=extension)
        except S3Error as exc:
            logger.exception("MinIO upload failed for project_id=%s", project_id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"MinIO upload failed: {exc.code}",
            ) from exc
        uploaded_storage_key = storage_key
        file_record = FileRecord(
            file_hash=file_hash,
            storage_bucket=storage.bucket,
            storage_key=storage_key,
            file_size=len(file_content),
            content_type=content_type,
            extension=extension,
        )
        created_new_file = True
    else:
        try:
            repaired_storage_key = _repair_missing_object_if_needed(
                file_record=file_record,
                storage=storage,
                file_content=file_content,
                content_type=content_type,
                extension=extension,
            )
        except S3Error as exc:
            logger.exception("Failed to verify/repair MinIO object for existing file_id=%s", file_record.id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"MinIO check failed: {exc.code}",
            ) from exc
        if repaired_storage_key is not None:
            repaired_existing_file = True
            uploaded_storage_key = repaired_storage_key
            logger.info("Repaired missing MinIO object for file_id=%s", file_record.id)
        else:
            logger.info("Reusing existing file_id=%s for hash=%s", file_record.id, file_hash)

    document = Document(
        file_id=file_record.id,
        project_id=project_id,
        software_id=software_id,
        name=document_name,
        description=description,
        extra_info=parsed_extra_info,
    )

    try:
        if created_new_file:
            session.add(file_record)
            try:
                session.flush()
            except IntegrityError:
                # Handle concurrent dedupe race: another request inserted the same hash first.
                session.rollback()
                if uploaded_storage_key is not None:
                    _cleanup_uploaded_object(storage, uploaded_storage_key)
                existing_file = _find_file_by_hash(file_hash, session)
                if existing_file is None:
                    logger.exception("File hash conflict but existing file not found, hash=%s", file_hash)
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="File dedupe conflict, please retry")
                file_record = existing_file
                document.file_id = file_record.id
        elif repaired_existing_file:
            session.add(file_record)

        session.add(document)
        session.commit()
        session.refresh(document)
    except IntegrityError as exc:
        session.rollback()
        if uploaded_storage_key is not None:
            _cleanup_uploaded_object(storage, uploaded_storage_key)
        logger.exception("Failed to create document record for project_id=%s", project_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document create conflict") from exc

    logger.info(
        "Document uploaded successfully, document_id=%s, file_id=%s, project_id=%s",
        document.id,
        document.file_id,
        project_id,
    )
    return document


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create_document(payload: DocumentCreate, session: Session = Depends(get_session)) -> Document:
    logger.info("Creating document by JSON payload, project_id=%s, file_id=%s", payload.project_id, payload.file_id)
    get_active_project_or_404(payload.project_id, session)
    if payload.software_id is not None:
        get_software_or_404(payload.software_id, session)
    if payload.file_id is not None:
        get_file_or_404(payload.file_id, session)

    document = Document(**payload.model_dump())
    try:
        session.add(document)
        session.commit()
        session.refresh(document)
    except IntegrityError as exc:
        session.rollback()
        logger.exception("Failed to create document by JSON payload, project_id=%s", payload.project_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document create conflict") from exc

    logger.info("Document created by JSON payload, document_id=%s", document.id)
    return document


@router.get("", response_model=list[DocumentRead])
def list_documents(
    session: Session = Depends(get_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    project_id: UUID | None = None,
    software_id: UUID | None = None,
) -> list[Document]:
    logger.info(
        "Listing documents, project_id=%s, software_id=%s, offset=%s, limit=%s",
        project_id,
        software_id,
        offset,
        limit,
    )
    statement = _build_documents_query(
        offset=offset,
        limit=limit,
        project_id=project_id,
        software_id=software_id,
    )
    return list(session.exec(statement).all())


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(document_id: UUID, session: Session = Depends(get_session)) -> Document:
    logger.info("Fetching document detail, document_id=%s", document_id)
    return get_active_document_or_404(document_id, session)


@router.get("/{document_id}/download")
def download_document(
    document_id: UUID,
    session: Session = Depends(get_session),
    storage: MinioStorage = Depends(get_minio_storage),
) -> Response:
    logger.info("Downloading document file, document_id=%s", document_id)
    document = get_active_document_or_404(document_id, session)
    if document.file_id is None:
        logger.warning("Document has no file_id, document_id=%s", document_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document file not found")

    file_record = get_file_or_404(document.file_id, session)
    try:
        payload = storage.download_bytes(file_record.storage_key)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}:
            logger.warning(
                "Document file object missing in MinIO, document_id=%s, file_id=%s, storage_key=%s",
                document_id,
                file_record.id,
                file_record.storage_key,
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File object not found in storage") from exc
        logger.exception("Failed to download document from MinIO, document_id=%s, file_id=%s", document_id, file_record.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO download failed: {exc.code}",
        ) from exc

    filename = _build_download_filename(document.name, file_record.extension)
    headers = {
        "Content-Disposition": _build_content_disposition(filename),
        "Content-Length": str(len(payload)),
    }
    logger.info(
        "Document file downloaded successfully, document_id=%s, file_id=%s, size=%s",
        document_id,
        file_record.id,
        len(payload),
    )
    return Response(content=payload, media_type=file_record.content_type, headers=headers)


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: UUID, payload: DocumentUpdate, session: Session = Depends(get_session)
) -> Document:
    logger.info("Updating document, document_id=%s", document_id)
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

    try:
        session.add(document)
        session.commit()
        session.refresh(document)
    except IntegrityError as exc:
        session.rollback()
        logger.exception("Failed to update document, document_id=%s", document_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document update conflict") from exc

    logger.info("Document updated, document_id=%s", document_id)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: UUID, session: Session = Depends(get_session)) -> Response:
    logger.info("Deleting document logically, document_id=%s", document_id)
    document = get_active_document_or_404(document_id, session)
    now = utc_now()
    document.deleted_at = now
    document.updated_at = now
    try:
        session.add(document)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        logger.exception("Failed to delete document logically, document_id=%s", document_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document delete conflict") from exc

    logger.info("Document deleted logically, document_id=%s", document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
