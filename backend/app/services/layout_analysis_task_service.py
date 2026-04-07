import asyncio
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from ..database import engine
from ..models import LayoutAnalysisTask, LayoutAnalysisTaskStatus
from ..models.common import utc_now
from .extracted_image_persistence_service import ExtractedImagePersistenceError, persist_extracted_images
from .file_convert_service import FileConvertServiceClient, get_file_convert_service_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LayoutAnalysisTaskSubmissionResult:
    task: LayoutAnalysisTask
    reused: bool


@dataclass(frozen=True)
class LayoutAnalysisModelSelection:
    requested_model: str | None
    target_model: str
    model_key: str


class UnsupportedLayoutAnalysisModelError(ValueError):
    pass



def _to_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}



def _normalize_optional_model(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None



def resolve_layout_analysis_model_selection(requested_model: str | None) -> LayoutAnalysisModelSelection:
    normalized_requested_model = _normalize_optional_model(requested_model)
    if normalized_requested_model is None:
        return LayoutAnalysisModelSelection(requested_model=None, target_model="marker", model_key="marker")

    normalized_target_model = normalized_requested_model.lower()
    if normalized_target_model != "marker":
        raise UnsupportedLayoutAnalysisModelError(
            f"Unsupported layout_model: {normalized_requested_model}. Only 'marker' is supported"
        )
    return LayoutAnalysisModelSelection(
        requested_model=normalized_requested_model,
        target_model="marker",
        model_key="marker",
    )



def get_active_layout_analysis_task_for_document(
    session: Session,
    *,
    document_id: UUID,
    layout_model_key: str,
) -> LayoutAnalysisTask | None:
    statement = (
        select(LayoutAnalysisTask)
        .where(
            LayoutAnalysisTask.document_id == document_id,
            LayoutAnalysisTask.layout_model_key == layout_model_key,
            LayoutAnalysisTask.status.in_((LayoutAnalysisTaskStatus.pending, LayoutAnalysisTaskStatus.running)),
        )
        .order_by(LayoutAnalysisTask.created_at.desc())
    )
    return session.exec(statement).first()



def get_latest_succeeded_layout_analysis_task_for_file(
    session: Session,
    *,
    file_id: UUID,
    layout_model_key: str,
) -> LayoutAnalysisTask | None:
    statement = (
        select(LayoutAnalysisTask)
        .where(
            LayoutAnalysisTask.file_id == file_id,
            LayoutAnalysisTask.layout_model_key == layout_model_key,
            LayoutAnalysisTask.status == LayoutAnalysisTaskStatus.succeeded,
        )
        .order_by(LayoutAnalysisTask.created_at.desc())
    )
    return session.exec(statement).first()



def create_or_reuse_layout_analysis_task(
    session: Session,
    *,
    document_id: UUID,
    file_id: UUID,
    storage_bucket: str,
    storage_key: str,
    requested_layout_model: str | None = None,
    force_layout_analysis: bool = False,
) -> LayoutAnalysisTaskSubmissionResult:
    model_selection = resolve_layout_analysis_model_selection(requested_layout_model)
    existing = get_active_layout_analysis_task_for_document(
        session,
        document_id=document_id,
        layout_model_key=model_selection.model_key,
    )
    if existing is not None:
        return LayoutAnalysisTaskSubmissionResult(task=existing, reused=True)

    layout_result_source_task: LayoutAnalysisTask | None = None
    if not force_layout_analysis:
        layout_result_source_task = get_latest_succeeded_layout_analysis_task_for_file(
            session,
            file_id=file_id,
            layout_model_key=model_selection.model_key,
        )
        if layout_result_source_task is not None:
            return LayoutAnalysisTaskSubmissionResult(task=layout_result_source_task, reused=True)

    task = LayoutAnalysisTask(
        document_id=document_id,
        file_id=file_id,
        storage_bucket=storage_bucket,
        storage_key=storage_key,
        requested_layout_model=model_selection.requested_model,
        target_layout_model=model_selection.target_model,
        layout_model_key=model_selection.model_key,
        force_layout_analysis=force_layout_analysis,
        layout_result_source_task_id=layout_result_source_task.id if layout_result_source_task is not None else None,
        status=LayoutAnalysisTaskStatus.pending,
    )
    session.add(task)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing_after_conflict = get_active_layout_analysis_task_for_document(
            session,
            document_id=document_id,
            layout_model_key=model_selection.model_key,
        )
        if existing_after_conflict is not None:
            return LayoutAnalysisTaskSubmissionResult(task=existing_after_conflict, reused=True)
        raise

    session.refresh(task)
    return LayoutAnalysisTaskSubmissionResult(task=task, reused=False)



def get_layout_analysis_task_by_id(session: Session, *, task_id: UUID) -> LayoutAnalysisTask | None:
    statement = select(LayoutAnalysisTask).where(LayoutAnalysisTask.id == task_id)
    return session.exec(statement).first()



def get_latest_layout_analysis_task_for_document_file(
    session: Session,
    *,
    document_id: UUID,
    file_id: UUID,
) -> LayoutAnalysisTask | None:
    statement = (
        select(LayoutAnalysisTask)
        .where(LayoutAnalysisTask.document_id == document_id, LayoutAnalysisTask.file_id == file_id)
        .order_by(LayoutAnalysisTask.created_at.desc())
    )
    return session.exec(statement).first()



def _mark_task_failed(session: Session, *, task: LayoutAnalysisTask, error_message: str) -> None:
    now = utc_now()
    task.status = LayoutAnalysisTaskStatus.failed
    task.error_message = error_message
    task.finished_at = now
    task.updated_at = now
    session.add(task)
    session.commit()



def recover_orphaned_layout_analysis_tasks() -> int:
    with Session(engine) as session:
        statement = select(LayoutAnalysisTask).where(LayoutAnalysisTask.status == LayoutAnalysisTaskStatus.running)
        running_tasks = list(session.exec(statement).all())
        if not running_tasks:
            return 0

        now = utc_now()
        for task in running_tasks:
            task.status = LayoutAnalysisTaskStatus.failed
            task.error_message = "Worker restarted before completion"
            task.finished_at = now
            task.updated_at = now
            session.add(task)

        session.commit()
        return len(running_tasks)



def claim_next_pending_layout_analysis_task_id() -> UUID | None:
    with Session(engine) as session:
        statement = (
            select(LayoutAnalysisTask)
            .where(LayoutAnalysisTask.status == LayoutAnalysisTaskStatus.pending)
            .order_by(LayoutAnalysisTask.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        task = session.exec(statement).first()
        if task is None:
            return None

        now = utc_now()
        task.status = LayoutAnalysisTaskStatus.running
        task.started_at = now
        task.finished_at = None
        task.updated_at = now
        task.error_message = None
        task.attempt_count += 1
        session.add(task)
        session.commit()
        return task.id



def _is_reusable_layout_result_source(task: LayoutAnalysisTask | None) -> bool:
    if task is None or task.status != LayoutAnalysisTaskStatus.succeeded:
        return False
    return task.markdown is not None and task.image_hashes is not None



def execute_layout_analysis_task(task_id: UUID, *, client: FileConvertServiceClient | None = None) -> None:
    file_convert_client = client or get_file_convert_service_client()

    with Session(engine) as session:
        task = session.get(LayoutAnalysisTask, task_id)
        if task is None:
            logger.warning("layout analysis task not found while executing, task_id=%s", task_id)
            return
        if task.status != LayoutAnalysisTaskStatus.running:
            logger.info("Skip layout analysis task execution due to unexpected status, task_id=%s, status=%s", task_id, task.status)
            return

        markdown: str | None = None
        image_hashes: dict[str, str] = {}

        if task.layout_result_source_task_id is not None:
            source_task = session.get(LayoutAnalysisTask, task.layout_result_source_task_id)
            if _is_reusable_layout_result_source(source_task):
                markdown = source_task.markdown
                image_hashes = dict(source_task.image_hashes or {})
            else:
                logger.info(
                    "Falling back to live layout analysis because cached source task is unavailable, task_id=%s, source_task_id=%s",
                    task_id,
                    task.layout_result_source_task_id,
                )
                task.layout_result_source_task_id = None

        if markdown is None:
            parsing_result, error = file_convert_client.convert_pdf_to_markdown(
                storage_key=task.storage_key,
                task_id=str(task.id),
                model=task.target_layout_model,
            )
            if error is not None or parsing_result is None:
                logger.warning("layout analysis task failed on file-convert-service, task_id=%s, error=%s", task_id, error)
                _mark_task_failed(session, task=task, error_message=error or "file-convert-service parsing failed")
                return

            markdown = parsing_result.markdown
            image_hashes = dict(parsing_result.image_hashes or {})
            try:
                persist_extracted_images(session, uploaded_images=parsing_result.uploaded_images)
            except (ExtractedImagePersistenceError, SQLAlchemyError):
                logger.exception("Failed to persist extracted images for layout analysis task, task_id=%s", task_id)
                _mark_task_failed(session, task=task, error_message="Failed to persist extracted images")
                return

        now = utc_now()
        task.status = LayoutAnalysisTaskStatus.succeeded
        task.markdown = markdown
        task.image_hashes = image_hashes
        task.error_message = None
        task.finished_at = now
        task.updated_at = now
        session.add(task)
        try:
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            logger.exception("Failed to mark layout analysis task succeeded, task_id=%s", task_id)
            try:
                _mark_task_failed(session, task=task, error_message="Failed to update layout analysis task state")
            except SQLAlchemyError:
                session.rollback()
                logger.exception("Failed to mark layout analysis task failed after success commit error, task_id=%s", task_id)
            return

    try:
        from .document_parsing_task_service import process_document_parsing_tasks_for_layout_task

        process_document_parsing_tasks_for_layout_task(task_id)
    except Exception:
        logger.exception("Failed to synchronize document parsing tasks after layout analysis completion, task_id=%s", task_id)



def process_one_pending_layout_analysis_task(*, client: FileConvertServiceClient | None = None) -> bool:
    task_id = claim_next_pending_layout_analysis_task_id()
    if task_id is None:
        return False

    execute_layout_analysis_task(task_id, client=client)
    return True


class LayoutAnalysisTaskWorker:
    def __init__(self, *, poll_interval_seconds: float = 1.0) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._runner_task is not None and not self._runner_task.done():
            return

        self._stop_event = asyncio.Event()
        recovered = await asyncio.to_thread(recover_orphaned_layout_analysis_tasks)
        if recovered > 0:
            logger.warning("Recovered orphaned running layout analysis tasks, count=%s", recovered)

        self._runner_task = asyncio.create_task(self._run_loop(), name="layout-analysis-task-worker")
        logger.info("layout analysis task worker started")

    async def stop(self) -> None:
        runner_task = self._runner_task
        if runner_task is None:
            return

        self._stop_event.set()
        await runner_task
        self._runner_task = None
        logger.info("layout analysis task worker stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                processed = await asyncio.to_thread(process_one_pending_layout_analysis_task)
            except Exception:
                logger.exception("Unexpected error while processing layout analysis task")
                processed = False

            if processed:
                continue

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                continue


@lru_cache(maxsize=1)
def get_layout_analysis_task_worker() -> LayoutAnalysisTaskWorker:
    poll_interval_seconds = float(
        os.getenv(
            "LAYOUT_ANALYSIS_TASK_WORKER_POLL_INTERVAL_SECONDS",
            os.getenv("DOCUMENT_PARSING_TASK_WORKER_POLL_INTERVAL_SECONDS", "1.0"),
        )
    )
    return LayoutAnalysisTaskWorker(poll_interval_seconds=poll_interval_seconds)



def is_layout_analysis_task_worker_enabled() -> bool:
    value = os.getenv("LAYOUT_ANALYSIS_TASK_WORKER_ENABLED")
    if value is None:
        value = os.getenv("DOCUMENT_PARSING_TASK_WORKER_ENABLED")
    return _to_bool(value, default=True)
