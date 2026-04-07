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
from ...models import FileRecord, LayoutAnalysisTask, LayoutAnalysisTaskStatus
from ...services.layout_analysis_task_service import (
    UnsupportedLayoutAnalysisModelError,
    create_or_reuse_layout_analysis_task,
    get_latest_layout_analysis_task_for_document_file,
    get_layout_analysis_task_by_id,
)
from ..dependencies import get_active_document_or_404, get_file_or_404

router = APIRouter(prefix="/layout-analysis", tags=["layout-analysis"])
logger = logging.getLogger(__name__)


class LayoutAnalysisTaskCreateRequest(BaseModel):
    document_id: UUID
    layout_model: str | None = None
    force_layout_analysis: bool = False


class LayoutAnalysisTaskRead(BaseModel):
    id: UUID
    document_id: UUID
    file_id: UUID
    storage_bucket: str
    storage_key: str
    requested_layout_model: str | None = None
    target_layout_model: str
    layout_model_key: str
    force_layout_analysis: bool = False
    layout_result_source_task_id: UUID | None = None
    layout_result_reused: bool = False
    status: LayoutAnalysisTaskStatus
    attempt_count: int
    reused: bool = False
    markdown: str | None = None
    image_hashes: dict[str, str] = Field(default_factory=dict)
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class LayoutAnalysisDocumentResultStatus(str, Enum):
    no_task = "no_task"
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class LayoutAnalysisDocumentResultRead(BaseModel):
    document_id: UUID
    file_id: UUID
    status: LayoutAnalysisDocumentResultStatus
    task_id: UUID | None = None
    storage_key: str | None = None
    requested_layout_model: str | None = None
    target_layout_model: str | None = None
    layout_model_key: str | None = None
    force_layout_analysis: bool = False
    layout_result_source_task_id: UUID | None = None
    layout_result_reused: bool = False
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



def _is_layout_result_reused(task: LayoutAnalysisTask) -> bool:
    return task.layout_result_source_task_id is not None



def _to_task_read(task: LayoutAnalysisTask, *, reused: bool = False) -> LayoutAnalysisTaskRead:
    return LayoutAnalysisTaskRead(
        id=task.id,
        document_id=task.document_id,
        file_id=task.file_id,
        storage_bucket=task.storage_bucket,
        storage_key=task.storage_key,
        requested_layout_model=task.requested_layout_model,
        target_layout_model=task.target_layout_model,
        layout_model_key=task.layout_model_key,
        force_layout_analysis=task.force_layout_analysis,
        layout_result_source_task_id=task.layout_result_source_task_id,
        layout_result_reused=_is_layout_result_reused(task),
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


@router.post("/tasks", response_model=LayoutAnalysisTaskRead, status_code=status.HTTP_202_ACCEPTED)
def create_layout_analysis_task(
    payload: LayoutAnalysisTaskCreateRequest,
    session: Session = Depends(get_session),
) -> LayoutAnalysisTaskRead:
    document_id, file_record = _resolve_pdf_file_record(payload.document_id, session)

    try:
        submission = create_or_reuse_layout_analysis_task(
            session,
            document_id=document_id,
            file_id=file_record.id,
            storage_bucket=file_record.storage_bucket,
            storage_key=file_record.storage_key,
            requested_layout_model=payload.layout_model,
            force_layout_analysis=payload.force_layout_analysis,
        )
    except UnsupportedLayoutAnalysisModelError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except IntegrityError as exc:
        session.rollback()
        logger.exception(
            "Failed to create layout analysis task",
            extra=build_log_extra(
                "layout_analysis.task_create.failed",
                document_id=str(payload.document_id),
                file_id=str(file_record.id),
            ),
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="layout analysis task conflict") from exc

    logger.info(
        "Layout analysis task submitted",
        extra=build_log_extra(
            "layout_analysis.task_create.succeeded",
            document_id=str(document_id),
            file_id=str(file_record.id),
            task_id=str(submission.task.id),
            reused=submission.reused,
            force_layout_analysis=payload.force_layout_analysis,
            layout_model=submission.task.target_layout_model,
        ),
    )
    return _to_task_read(submission.task, reused=submission.reused)


@router.get("/tasks/{task_id}", response_model=LayoutAnalysisTaskRead)
def get_layout_analysis_task(task_id: UUID, session: Session = Depends(get_session)) -> LayoutAnalysisTaskRead:
    task = get_layout_analysis_task_by_id(session, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="layout analysis task not found")
    return _to_task_read(task)


@router.get("/results/{document_id}", response_model=LayoutAnalysisDocumentResultRead)
def get_layout_analysis_document_result(
    document_id: UUID,
    session: Session = Depends(get_session),
) -> LayoutAnalysisDocumentResultRead:
    resolved_document_id, file_record = _resolve_pdf_file_record(document_id, session)

    task = get_latest_layout_analysis_task_for_document_file(
        session,
        document_id=resolved_document_id,
        file_id=file_record.id,
    )
    if task is None:
        return LayoutAnalysisDocumentResultRead(
            document_id=resolved_document_id,
            file_id=file_record.id,
            status=LayoutAnalysisDocumentResultStatus.no_task,
            storage_key=file_record.storage_key,
        )

    return LayoutAnalysisDocumentResultRead(
        document_id=resolved_document_id,
        file_id=file_record.id,
        status=LayoutAnalysisDocumentResultStatus(task.status.value),
        task_id=task.id,
        storage_key=task.storage_key,
        requested_layout_model=task.requested_layout_model,
        target_layout_model=task.target_layout_model,
        layout_model_key=task.layout_model_key,
        force_layout_analysis=task.force_layout_analysis,
        layout_result_source_task_id=task.layout_result_source_task_id,
        layout_result_reused=_is_layout_result_reused(task),
        markdown=task.markdown if task.status == LayoutAnalysisTaskStatus.succeeded else None,
        image_hashes=task.image_hashes if task.status == LayoutAnalysisTaskStatus.succeeded else {},
        error_message=task.error_message if task.status == LayoutAnalysisTaskStatus.failed else None,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        updated_at=task.updated_at,
    )
