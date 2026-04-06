"""文档解析路由。

职责：
1. 暴露 file-convert-service 可用性检查接口。
2. 提供 PDF 同步解析接口与异步任务接口。
3. 将文档、文件与任务服务结果映射为 API 响应模型。

说明：
- 当前仅支持 PDF 文档解析。
- 同步解析成功后会尝试落库 extracted_images 元数据。
"""

import logging
from datetime import datetime
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from ...core.logging import build_log_extra
from ...database import get_session
from ...models import DocumentParsingTask, DocumentParsingTaskStatus, FileRecord
from ...services import FileConvertServiceClient
from ...services.document_parsing_task_service import (
    create_or_reuse_document_parsing_task,
    get_document_parsing_task_by_id,
    get_latest_document_parsing_task_for_document_file,
)
from ...services.extracted_image_persistence_service import (
    ExtractedImagePersistenceError,
    persist_extracted_images,
)
from ..dependencies import (
    get_active_document_or_404,
    get_file_convert_service_client,
    get_file_or_404,
)

router = APIRouter(prefix="/document-parsing", tags=["document-parsing"])
logger = logging.getLogger(__name__)


class DocumentParsingAvailabilityRead(BaseModel):
    available: bool
    service: str
    health_path: str | None = None
    error: str | None = None


class PdfToMarkdownParseRequest(BaseModel):
    document_id: UUID


class PdfToMarkdownParseRead(BaseModel):
    document_id: UUID
    storage_key: str
    markdown: str
    image_hashes: dict[str, str] = Field(default_factory=dict)


class PdfToMarkdownTaskCreateRequest(BaseModel):
    document_id: UUID


class PdfToMarkdownTaskRead(BaseModel):
    id: UUID
    document_id: UUID
    file_id: UUID
    storage_bucket: str
    storage_key: str
    status: DocumentParsingTaskStatus
    attempt_count: int
    reused: bool = False
    markdown: str | None = None
    image_hashes: dict[str, str] = Field(default_factory=dict)
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class PdfToMarkdownDocumentResultStatus(str, Enum):
    no_task = "no_task"
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class PdfToMarkdownDocumentResultRead(BaseModel):
    document_id: UUID
    file_id: UUID
    status: PdfToMarkdownDocumentResultStatus
    task_id: UUID | None = None
    storage_key: str | None = None
    markdown: str | None = None
    image_hashes: dict[str, str] = Field(default_factory=dict)
    error_message: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None


def _resolve_pdf_file_record(document_id: UUID, session: Session) -> tuple[UUID, FileRecord]:
    document = get_active_document_or_404(document_id, session)
    if document.file_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document file not found")

    file_record = get_file_or_404(document.file_id, session)
    if file_record.extension.lower() != ".pdf":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only PDF document is supported")

    return document.id, file_record


def _to_task_read(task: DocumentParsingTask, *, reused: bool = False) -> PdfToMarkdownTaskRead:
    return PdfToMarkdownTaskRead(
        id=task.id,
        document_id=task.document_id,
        file_id=task.file_id,
        storage_bucket=task.storage_bucket,
        storage_key=task.storage_key,
        status=task.status,
        attempt_count=task.attempt_count,
        reused=reused,
        markdown=task.markdown,
        image_hashes=task.image_hashes,
        error_message=task.error_message,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        updated_at=task.updated_at,
    )


@router.get("/availability", response_model=DocumentParsingAvailabilityRead)
def get_document_parsing_availability(
    client: FileConvertServiceClient = Depends(get_file_convert_service_client),
) -> DocumentParsingAvailabilityRead:
    available, error = client.check_availability()
    if available:
        return DocumentParsingAvailabilityRead(
            available=True,
            service="file-convert-service",
            health_path="/health",
        )

    logger.warning(
        "file-convert-service is unavailable",
        extra=build_log_extra("document_parsing.availability.unavailable", error=error),
    )
    return DocumentParsingAvailabilityRead(
        available=False,
        service="file-convert-service",
        error=error,
    )


@router.post("/pdf-to-markdown/tasks", response_model=PdfToMarkdownTaskRead)
def create_pdf_to_markdown_task(
    payload: PdfToMarkdownTaskCreateRequest,
    session: Session = Depends(get_session),
) -> PdfToMarkdownTaskRead:
    document_id, file_record = _resolve_pdf_file_record(payload.document_id, session)

    try:
        submission = create_or_reuse_document_parsing_task(
            session,
            document_id=document_id,
            file_id=file_record.id,
            storage_bucket=file_record.storage_bucket,
            storage_key=file_record.storage_key,
        )
    except IntegrityError as exc:
        session.rollback()
        logger.exception(
            "Failed to create document parsing task",
            extra=build_log_extra(
                "document_parsing.task_create.failed",
                document_id=str(payload.document_id),
                file_id=str(file_record.id),
            ),
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="document parsing task conflict") from exc

    logger.info(
        "Document parsing task submitted",
        extra=build_log_extra(
            "document_parsing.task_create.succeeded",
            document_id=str(document_id),
            file_id=str(file_record.id),
            task_id=str(submission.task.id),
            reused=submission.reused,
        ),
    )
    return _to_task_read(submission.task, reused=submission.reused)


@router.get("/pdf-to-markdown/tasks/{task_id}", response_model=PdfToMarkdownTaskRead)
def get_pdf_to_markdown_task(task_id: UUID, session: Session = Depends(get_session)) -> PdfToMarkdownTaskRead:
    task = get_document_parsing_task_by_id(session, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document parsing task not found")
    return _to_task_read(task)


@router.get("/pdf-to-markdown/results/{document_id}", response_model=PdfToMarkdownDocumentResultRead)
def get_pdf_to_markdown_document_result(
    document_id: UUID,
    session: Session = Depends(get_session),
) -> PdfToMarkdownDocumentResultRead:
    resolved_document_id, file_record = _resolve_pdf_file_record(document_id, session)

    task = get_latest_document_parsing_task_for_document_file(
        session,
        document_id=resolved_document_id,
        file_id=file_record.id,
    )
    if task is None:
        return PdfToMarkdownDocumentResultRead(
            document_id=resolved_document_id,
            file_id=file_record.id,
            status=PdfToMarkdownDocumentResultStatus.no_task,
            storage_key=file_record.storage_key,
            image_hashes={},
        )

    result_status = PdfToMarkdownDocumentResultStatus(task.status.value)
    return PdfToMarkdownDocumentResultRead(
        document_id=resolved_document_id,
        file_id=file_record.id,
        status=result_status,
        task_id=task.id,
        storage_key=task.storage_key,
        markdown=task.markdown if task.status == DocumentParsingTaskStatus.succeeded else None,
        image_hashes=task.image_hashes if task.status == DocumentParsingTaskStatus.succeeded else {},
        error_message=task.error_message if task.status == DocumentParsingTaskStatus.failed else None,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        updated_at=task.updated_at,
    )


@router.post("/pdf-to-markdown", response_model=PdfToMarkdownParseRead)
def parse_pdf_to_markdown(
    payload: PdfToMarkdownParseRequest,
    session: Session = Depends(get_session),
    client: FileConvertServiceClient = Depends(get_file_convert_service_client),
) -> PdfToMarkdownParseRead:
    document_id, file_record = _resolve_pdf_file_record(payload.document_id, session)

    parsing_result, error = client.convert_pdf_to_markdown(storage_key=file_record.storage_key)
    if error is not None:
        logger.warning(
            "file-convert-service parsing failed",
            extra=build_log_extra(
                "document_parsing.sync.failed",
                document_id=str(payload.document_id),
                storage_key=file_record.storage_key,
                error=error,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"file-convert-service parsing failed: {error}",
        )

    if parsing_result is not None:
        try:
            persist_extracted_images(session, uploaded_images=parsing_result.uploaded_images)
        except ExtractedImagePersistenceError as exc:
            logger.exception(
                "Failed to persist extracted images after document parsing",
                extra=build_log_extra(
                    "document_parsing.persist_extracted_images.failed",
                    document_id=str(payload.document_id),
                    storage_key=file_record.storage_key,
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to persist extracted images",
            ) from exc

    logger.info(
        "Document parsing finished",
        extra=build_log_extra(
            "document_parsing.sync.succeeded",
            document_id=str(document_id),
            storage_key=file_record.storage_key,
            extracted_image_count=len(parsing_result.uploaded_images if parsing_result is not None else []),
        ),
    )
    return PdfToMarkdownParseRead(
        document_id=document_id,
        storage_key=file_record.storage_key,
        markdown=parsing_result.markdown if parsing_result is not None else "",
        image_hashes=parsing_result.image_hashes if parsing_result is not None else {},
    )
