import asyncio
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from ..database import engine
from ..models import DocumentParsingTask, DocumentParsingTaskStatus
from ..models.common import utc_now
from .extracted_image_persistence_service import ExtractedImagePersistenceError, persist_extracted_images
from .file_convert_service import FileConvertServiceClient, get_file_convert_service_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentParsingTaskSubmissionResult:
    task: DocumentParsingTask
    reused: bool


def _to_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_active_document_parsing_task_for_document(session: Session, *, document_id: UUID) -> DocumentParsingTask | None:
    statement = (
        select(DocumentParsingTask)
        .where(
            DocumentParsingTask.document_id == document_id,
            DocumentParsingTask.status.in_((DocumentParsingTaskStatus.pending, DocumentParsingTaskStatus.running)),
        )
        .order_by(DocumentParsingTask.created_at.desc())
    )
    return session.exec(statement).first()


def create_or_reuse_document_parsing_task(
    session: Session,
    *,
    document_id: UUID,
    file_id: UUID,
    storage_bucket: str,
    storage_key: str,
) -> DocumentParsingTaskSubmissionResult:
    existing = get_active_document_parsing_task_for_document(session, document_id=document_id)
    if existing is not None:
        return DocumentParsingTaskSubmissionResult(task=existing, reused=True)

    task = DocumentParsingTask(
        document_id=document_id,
        file_id=file_id,
        storage_bucket=storage_bucket,
        storage_key=storage_key,
        status=DocumentParsingTaskStatus.pending,
    )
    session.add(task)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing_after_conflict = get_active_document_parsing_task_for_document(session, document_id=document_id)
        if existing_after_conflict is not None:
            return DocumentParsingTaskSubmissionResult(task=existing_after_conflict, reused=True)
        raise

    session.refresh(task)
    return DocumentParsingTaskSubmissionResult(task=task, reused=False)


def get_document_parsing_task_by_id(session: Session, *, task_id: UUID) -> DocumentParsingTask | None:
    statement = select(DocumentParsingTask).where(DocumentParsingTask.id == task_id)
    return session.exec(statement).first()


def get_latest_document_parsing_task_for_document_file(
    session: Session,
    *,
    document_id: UUID,
    file_id: UUID,
) -> DocumentParsingTask | None:
    statement = (
        select(DocumentParsingTask)
        .where(
            DocumentParsingTask.document_id == document_id,
            DocumentParsingTask.file_id == file_id,
        )
        .order_by(DocumentParsingTask.created_at.desc())
    )
    return session.exec(statement).first()


def _mark_task_failed(session: Session, *, task: DocumentParsingTask, error_message: str) -> None:
    now = utc_now()
    task.status = DocumentParsingTaskStatus.failed
    task.error_message = error_message
    task.finished_at = now
    task.updated_at = now
    session.add(task)
    session.commit()


def recover_orphaned_document_parsing_tasks() -> int:
    with Session(engine) as session:
        statement = select(DocumentParsingTask).where(DocumentParsingTask.status == DocumentParsingTaskStatus.running)
        running_tasks = list(session.exec(statement).all())
        if not running_tasks:
            return 0

        now = utc_now()
        for task in running_tasks:
            task.status = DocumentParsingTaskStatus.failed
            task.error_message = "Worker restarted before completion"
            task.finished_at = now
            task.updated_at = now
            session.add(task)

        session.commit()
        return len(running_tasks)


def claim_next_pending_document_parsing_task_id() -> UUID | None:
    with Session(engine) as session:
        statement = (
            select(DocumentParsingTask)
            .where(DocumentParsingTask.status == DocumentParsingTaskStatus.pending)
            .order_by(DocumentParsingTask.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        task = session.exec(statement).first()
        if task is None:
            return None

        now = utc_now()
        task.status = DocumentParsingTaskStatus.running
        task.started_at = now
        task.finished_at = None
        task.updated_at = now
        task.error_message = None
        task.attempt_count += 1
        session.add(task)
        session.commit()
        return task.id


def execute_document_parsing_task(task_id: UUID, *, client: FileConvertServiceClient | None = None) -> None:
    file_convert_client = client or get_file_convert_service_client()

    with Session(engine) as session:
        task = session.get(DocumentParsingTask, task_id)
        if task is None:
            logger.warning("document parsing task not found while executing, task_id=%s", task_id)
            return
        if task.status != DocumentParsingTaskStatus.running:
            logger.info("Skip document parsing task execution due to unexpected status, task_id=%s, status=%s", task_id, task.status)
            return

        parsing_result, error = file_convert_client.convert_pdf_to_markdown(
            storage_key=task.storage_key,
            task_id=str(task.id),
        )
        if error is not None or parsing_result is None:
            logger.warning("document parsing task failed on file-convert-service, task_id=%s, error=%s", task_id, error)
            _mark_task_failed(
                session,
                task=task,
                error_message=error or "file-convert-service parsing failed",
            )
            return

        try:
            persist_extracted_images(session, uploaded_images=parsing_result.uploaded_images)
        except ExtractedImagePersistenceError:
            logger.exception("Failed to persist extracted images for document parsing task, task_id=%s", task_id)
            _mark_task_failed(
                session,
                task=task,
                error_message="Failed to persist extracted images",
            )
            return

        now = utc_now()
        task.status = DocumentParsingTaskStatus.succeeded
        task.markdown = parsing_result.markdown
        task.image_hashes = parsing_result.image_hashes
        task.error_message = None
        task.finished_at = now
        task.updated_at = now
        session.add(task)
        try:
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            logger.exception("Failed to mark document parsing task succeeded, task_id=%s", task_id)
            try:
                _mark_task_failed(
                    session,
                    task=task,
                    error_message="Failed to update document parsing task state",
                )
            except SQLAlchemyError:
                session.rollback()
                logger.exception("Failed to mark document parsing task failed after success commit error, task_id=%s", task_id)


def process_one_pending_document_parsing_task(*, client: FileConvertServiceClient | None = None) -> bool:
    task_id = claim_next_pending_document_parsing_task_id()
    if task_id is None:
        return False

    execute_document_parsing_task(task_id, client=client)
    return True


class DocumentParsingTaskWorker:
    def __init__(self, *, poll_interval_seconds: float = 1.0) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._runner_task is not None and not self._runner_task.done():
            return

        self._stop_event = asyncio.Event()
        recovered = await asyncio.to_thread(recover_orphaned_document_parsing_tasks)
        if recovered > 0:
            logger.warning("Recovered orphaned running document parsing tasks, count=%s", recovered)

        self._runner_task = asyncio.create_task(self._run_loop(), name="document-parsing-task-worker")
        logger.info("document parsing task worker started")

    async def stop(self) -> None:
        runner_task = self._runner_task
        if runner_task is None:
            return

        self._stop_event.set()
        await runner_task
        self._runner_task = None
        logger.info("document parsing task worker stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                processed = await asyncio.to_thread(process_one_pending_document_parsing_task)
            except Exception:
                logger.exception("Unexpected error while processing document parsing task")
                processed = False

            if processed:
                continue

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                continue


@lru_cache(maxsize=1)
def get_document_parsing_task_worker() -> DocumentParsingTaskWorker:
    poll_interval_seconds = float(os.getenv("DOCUMENT_PARSING_TASK_WORKER_POLL_INTERVAL_SECONDS", "1.0"))
    return DocumentParsingTaskWorker(poll_interval_seconds=poll_interval_seconds)


def is_document_parsing_task_worker_enabled() -> bool:
    value = os.getenv("DOCUMENT_PARSING_TASK_WORKER_ENABLED")
    return _to_bool(value, default=True)


