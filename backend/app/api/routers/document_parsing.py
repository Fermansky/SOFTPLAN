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
from ...models import (
    DocumentParsingImageItem,
    DocumentParsingImageItemResultSource,
    DocumentParsingImageItemStatus,
    DocumentParsingTask,
    DocumentParsingTaskStatus,
    FileRecord,
    LayoutAnalysisTask,
    LayoutAnalysisTaskStatus,
)
from ...services import FileConvertServiceClient
from ...services.document_parsing_task_service import (
    DocumentParsingImageSemanticResult,
    create_or_reuse_document_parsing_task,
    get_document_parsing_image_items,
    get_document_parsing_image_semantic_result,
    get_document_parsing_task_by_id,
    get_latest_succeeded_document_parsing_task_for_document_file,
    get_layout_task_for_document_parsing_task,
)
from ...services.layout_analysis_task_service import UnsupportedLayoutAnalysisModelError
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


class DocumentParsingImageAnalysisStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class DocumentParsingTaskCreateRequest(BaseModel):
    document_id: UUID
    layout_model: str | None = None
    image_model: str | None = None
    force_layout_analysis: bool = False


class DocumentParsingImageSemanticRead(BaseModel):
    description: str
    result_model: str | None = None
    source_task_id: UUID | None = None
    updated_at: datetime


class DocumentParsingImageItemRead(BaseModel):
    id: int
    source_key: str
    file_hash: str
    extracted_image_id: int
    semantic_task_id: UUID | None = None
    status: DocumentParsingImageItemStatus
    result_source: DocumentParsingImageItemResultSource | None = None
    error_message: str | None = None
    semantic: DocumentParsingImageSemanticRead | None = None
    created_at: datetime
    updated_at: datetime


class DocumentParsingTaskRead(BaseModel):
    id: UUID
    document_id: UUID
    file_id: UUID
    storage_bucket: str
    storage_key: str
    requested_layout_model: str | None = None
    target_layout_model: str
    layout_model_key: str
    requested_image_model: str | None = None
    target_image_model: str | None = None
    image_model_key: str
    force_layout_analysis: bool = False
    layout_task_id: UUID
    status: DocumentParsingTaskStatus
    layout_status: LayoutAnalysisTaskStatus
    image_analysis_status: DocumentParsingImageAnalysisStatus
    image_total_count: int = 0
    image_succeeded_count: int = 0
    image_failed_count: int = 0
    reused: bool = False
    markdown: str | None = None
    image_hashes: dict[str, str] = Field(default_factory=dict)
    image_items: list[DocumentParsingImageItemRead] = Field(default_factory=list)
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class DocumentParsingDocumentResultStatus(str, Enum):
    no_task = "no_task"
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class DocumentParsingDocumentResultRead(BaseModel):
    document_id: UUID
    file_id: UUID
    status: DocumentParsingDocumentResultStatus
    task_id: UUID | None = None
    storage_key: str | None = None
    requested_layout_model: str | None = None
    target_layout_model: str | None = None
    layout_model_key: str | None = None
    requested_image_model: str | None = None
    target_image_model: str | None = None
    image_model_key: str | None = None
    force_layout_analysis: bool = False
    layout_task_id: UUID | None = None
    layout_status: LayoutAnalysisTaskStatus | None = None
    image_analysis_status: DocumentParsingImageAnalysisStatus | None = None
    image_total_count: int = 0
    image_succeeded_count: int = 0
    image_failed_count: int = 0
    markdown: str | None = None
    image_hashes: dict[str, str] = Field(default_factory=dict)
    image_items: list[DocumentParsingImageItemRead] = Field(default_factory=list)
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



def _to_image_semantic_read(
    semantic: DocumentParsingImageSemanticResult | None,
) -> DocumentParsingImageSemanticRead | None:
    if semantic is None:
        return None
    return DocumentParsingImageSemanticRead(
        description=semantic.description,
        result_model=semantic.result_model,
        source_task_id=semantic.source_task_id,
        updated_at=semantic.updated_at,
    )


def _to_image_item_read(
    item: DocumentParsingImageItem,
    *,
    semantic: DocumentParsingImageSemanticResult | None = None,
) -> DocumentParsingImageItemRead:
    return DocumentParsingImageItemRead(
        id=item.id or 0,
        source_key=item.source_key,
        file_hash=item.file_hash,
        extracted_image_id=item.extracted_image_id,
        semantic_task_id=item.semantic_task_id,
        status=item.status,
        result_source=item.result_source,
        error_message=item.error_message,
        semantic=_to_image_semantic_read(semantic),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )



def _get_image_analysis_status(
    task: DocumentParsingTask,
    *,
    layout_task: LayoutAnalysisTask | None,
) -> DocumentParsingImageAnalysisStatus:
    if layout_task is None:
        return DocumentParsingImageAnalysisStatus.failed if task.status == DocumentParsingTaskStatus.failed else DocumentParsingImageAnalysisStatus.pending
    if layout_task.status == LayoutAnalysisTaskStatus.failed:
        return DocumentParsingImageAnalysisStatus.failed
    if layout_task.status == LayoutAnalysisTaskStatus.pending:
        return DocumentParsingImageAnalysisStatus.pending
    if layout_task.status == LayoutAnalysisTaskStatus.running:
        return DocumentParsingImageAnalysisStatus.running
    if task.image_failed_count > 0:
        return DocumentParsingImageAnalysisStatus.failed
    if task.image_total_count == 0 or task.image_succeeded_count == task.image_total_count:
        return DocumentParsingImageAnalysisStatus.succeeded
    return DocumentParsingImageAnalysisStatus.running



def _to_document_parsing_task_read(
    session: Session,
    task: DocumentParsingTask,
    *,
    reused: bool = False,
) -> DocumentParsingTaskRead:
    layout_task = get_layout_task_for_document_parsing_task(session, task=task)
    image_items = get_document_parsing_image_items(session, task_id=task.id)
    image_item_reads = [
        _to_image_item_read(
            item,
            semantic=get_document_parsing_image_semantic_result(
                session,
                item=item,
                image_model_key=task.image_model_key,
            ),
        )
        for item in image_items
    ]
    layout_status = layout_task.status if layout_task is not None else LayoutAnalysisTaskStatus.failed
    layout_succeeded = layout_task is not None and layout_task.status == LayoutAnalysisTaskStatus.succeeded
    return DocumentParsingTaskRead(
        id=task.id,
        document_id=task.document_id,
        file_id=task.file_id,
        storage_bucket=task.storage_bucket,
        storage_key=task.storage_key,
        requested_layout_model=task.requested_layout_model,
        target_layout_model=task.target_layout_model,
        layout_model_key=task.layout_model_key,
        requested_image_model=task.requested_image_model,
        target_image_model=task.target_image_model,
        image_model_key=task.image_model_key,
        force_layout_analysis=task.force_layout_analysis,
        layout_task_id=task.layout_task_id,
        status=task.status,
        layout_status=layout_status,
        image_analysis_status=_get_image_analysis_status(task, layout_task=layout_task),
        image_total_count=task.image_total_count,
        image_succeeded_count=task.image_succeeded_count,
        image_failed_count=task.image_failed_count,
        reused=reused,
        markdown=task.markdown if layout_succeeded else None,
        image_hashes=task.image_hashes if layout_succeeded else {},
        image_items=image_item_reads,
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
        return DocumentParsingAvailabilityRead(available=True, service="file-convert-service", health_path="/health")

    logger.warning(
        "file-convert-service is unavailable",
        extra=build_log_extra("document_parsing.availability.unavailable", error=error),
    )
    return DocumentParsingAvailabilityRead(available=False, service="file-convert-service", error=error)


@router.post("/tasks", response_model=DocumentParsingTaskRead, status_code=status.HTTP_202_ACCEPTED)
def create_document_parsing_task(
    payload: DocumentParsingTaskCreateRequest,
    session: Session = Depends(get_session),
) -> DocumentParsingTaskRead:
    document_id, file_record = _resolve_pdf_file_record(payload.document_id, session)

    try:
        submission = create_or_reuse_document_parsing_task(
            session,
            document_id=document_id,
            file_id=file_record.id,
            storage_bucket=file_record.storage_bucket,
            storage_key=file_record.storage_key,
            requested_layout_model=payload.layout_model,
            requested_image_model=payload.image_model,
            force_layout_analysis=payload.force_layout_analysis,
        )
    except UnsupportedLayoutAnalysisModelError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
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
            force_layout_analysis=payload.force_layout_analysis,
            layout_model=submission.task.target_layout_model,
            image_model=submission.task.target_image_model,
        ),
    )
    return _to_document_parsing_task_read(session, submission.task, reused=submission.reused)


@router.get("/tasks/{task_id}", response_model=DocumentParsingTaskRead)
def get_document_parsing_task(task_id: UUID, session: Session = Depends(get_session)) -> DocumentParsingTaskRead:
    task = get_document_parsing_task_by_id(session, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document parsing task not found")
    return _to_document_parsing_task_read(session, task)


@router.get("/results/{document_id}", response_model=DocumentParsingDocumentResultRead)
def get_document_parsing_document_result(
    document_id: UUID,
    session: Session = Depends(get_session),
) -> DocumentParsingDocumentResultRead:
    resolved_document_id, file_record = _resolve_pdf_file_record(document_id, session)

    task = get_latest_succeeded_document_parsing_task_for_document_file(
        session,
        document_id=resolved_document_id,
        file_id=file_record.id,
    )
    if task is None:
        return DocumentParsingDocumentResultRead(
            document_id=resolved_document_id,
            file_id=file_record.id,
            status=DocumentParsingDocumentResultStatus.no_task,
            storage_key=file_record.storage_key,
        )

    task_read = _to_document_parsing_task_read(session, task)
    return DocumentParsingDocumentResultRead(
        document_id=resolved_document_id,
        file_id=file_record.id,
        status=DocumentParsingDocumentResultStatus(task.status.value),
        task_id=task.id,
        storage_key=task.storage_key,
        requested_layout_model=task.requested_layout_model,
        target_layout_model=task.target_layout_model,
        layout_model_key=task.layout_model_key,
        requested_image_model=task.requested_image_model,
        target_image_model=task.target_image_model,
        image_model_key=task.image_model_key,
        force_layout_analysis=task.force_layout_analysis,
        layout_task_id=task.layout_task_id,
        layout_status=task_read.layout_status,
        image_analysis_status=task_read.image_analysis_status,
        image_total_count=task.image_total_count,
        image_succeeded_count=task.image_succeeded_count,
        image_failed_count=task.image_failed_count,
        markdown=task_read.markdown,
        image_hashes=task_read.image_hashes,
        image_items=task_read.image_items,
        error_message=task.error_message,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        updated_at=task.updated_at,
    )
