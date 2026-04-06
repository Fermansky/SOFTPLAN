import asyncio
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from ..database import engine
from ..models import ExtractedImage, ExtractedImageSemanticTask, ExtractedImageSemanticTaskStatus
from ..models.common import utc_now
from .extracted_image_semantic_service import (
    execute_extracted_image_semantic_recognition,
    get_extracted_image_semantic_prompt_snapshot,
    get_extracted_image_semantic_target_model_key,
    resolve_extracted_image_semantic_model,
)
from .llm_service import LlmServiceClient, get_llm_service_client
from .minio_storage import MinioStorage, get_minio_storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractedImageSemanticTaskSubmissionResult:
    task: ExtractedImageSemanticTask
    reused: bool


def _to_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_requested_model(requested_model: str | None) -> str | None:
    if requested_model is None:
        return None
    stripped = requested_model.strip()
    return stripped or None


def _has_semantic_snapshot(extracted_image: ExtractedImage) -> bool:
    description = extracted_image.semantic_description
    if description is None:
        return False
    return bool(description.strip())


def _should_update_extracted_image_semantic_snapshot(
    extracted_image: ExtractedImage,
    *,
    overwrite_existing_snapshot: bool,
) -> bool:
    return overwrite_existing_snapshot or not _has_semantic_snapshot(extracted_image)


def get_active_extracted_image_semantic_task(
    session: Session,
    *,
    extracted_image_id: int,
    target_model_key: str,
    overwrite_existing_snapshot: bool,
) -> ExtractedImageSemanticTask | None:
    statement = (
        select(ExtractedImageSemanticTask)
        .where(
            ExtractedImageSemanticTask.extracted_image_id == extracted_image_id,
            ExtractedImageSemanticTask.target_model_key == target_model_key,
            ExtractedImageSemanticTask.overwrite_existing_snapshot == overwrite_existing_snapshot,
            ExtractedImageSemanticTask.status.in_(
                (ExtractedImageSemanticTaskStatus.pending, ExtractedImageSemanticTaskStatus.running)
            ),
        )
        .order_by(ExtractedImageSemanticTask.created_at.desc())
    )
    return session.exec(statement).first()


def create_or_reuse_extracted_image_semantic_task(
    session: Session,
    *,
    extracted_image: ExtractedImage,
    requested_model: str | None = None,
    request_id: str | None = None,
    overwrite_existing_snapshot: bool = False,
) -> ExtractedImageSemanticTaskSubmissionResult:
    normalized_requested_model = _normalize_requested_model(requested_model)
    target_model = resolve_extracted_image_semantic_model(normalized_requested_model)
    target_model_key = get_extracted_image_semantic_target_model_key(target_model)
    prompt_path, prompt_hash = get_extracted_image_semantic_prompt_snapshot()

    existing = get_active_extracted_image_semantic_task(
        session,
        extracted_image_id=extracted_image.id or 0,
        target_model_key=target_model_key,
        overwrite_existing_snapshot=overwrite_existing_snapshot,
    )
    if existing is not None:
        return ExtractedImageSemanticTaskSubmissionResult(task=existing, reused=True)

    task = ExtractedImageSemanticTask(
        extracted_image_id=extracted_image.id or 0,
        status=ExtractedImageSemanticTaskStatus.pending,
        requested_model=normalized_requested_model,
        target_model=target_model,
        target_model_key=target_model_key,
        overwrite_existing_snapshot=overwrite_existing_snapshot,
        result_model=None,
        request_id=request_id,
        prompt_path=prompt_path,
        prompt_hash=prompt_hash,
        description=None,
        error_message=None,
        attempt_count=0,
    )
    session.add(task)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing_after_conflict = get_active_extracted_image_semantic_task(
            session,
            extracted_image_id=extracted_image.id or 0,
            target_model_key=target_model_key,
            overwrite_existing_snapshot=overwrite_existing_snapshot,
        )
        if existing_after_conflict is not None:
            return ExtractedImageSemanticTaskSubmissionResult(task=existing_after_conflict, reused=True)
        raise

    session.refresh(task)
    return ExtractedImageSemanticTaskSubmissionResult(task=task, reused=False)


def get_extracted_image_semantic_task_by_id(
    session: Session,
    *,
    task_id: UUID,
) -> ExtractedImageSemanticTask | None:
    statement = select(ExtractedImageSemanticTask).where(ExtractedImageSemanticTask.id == task_id)
    return session.exec(statement).first()


def get_latest_extracted_image_semantic_task_for_image(
    session: Session,
    *,
    extracted_image_id: int,
) -> ExtractedImageSemanticTask | None:
    statement = (
        select(ExtractedImageSemanticTask)
        .where(ExtractedImageSemanticTask.extracted_image_id == extracted_image_id)
        .order_by(ExtractedImageSemanticTask.created_at.desc())
    )
    return session.exec(statement).first()


def _mark_task_failed(session: Session, *, task: ExtractedImageSemanticTask, error_message: str) -> None:
    now = utc_now()
    task.status = ExtractedImageSemanticTaskStatus.failed
    task.error_message = error_message
    task.finished_at = now
    task.updated_at = now
    session.add(task)
    session.commit()


def _update_extracted_image_semantic_snapshot(
    extracted_image: ExtractedImage,
    *,
    description: str,
    result_model: str | None,
    updated_at,
) -> None:
    extracted_image.semantic_description = description
    extracted_image.semantic_description_model = result_model
    extracted_image.semantic_description_updated_at = updated_at


def recover_orphaned_extracted_image_semantic_tasks() -> int:
    with Session(engine) as session:
        statement = select(ExtractedImageSemanticTask).where(
            ExtractedImageSemanticTask.status == ExtractedImageSemanticTaskStatus.running
        )
        running_tasks = list(session.exec(statement).all())
        if not running_tasks:
            return 0

        now = utc_now()
        for task in running_tasks:
            task.status = ExtractedImageSemanticTaskStatus.failed
            task.error_message = "Worker restarted before completion"
            task.finished_at = now
            task.updated_at = now
            session.add(task)

        session.commit()
        return len(running_tasks)


def claim_next_pending_extracted_image_semantic_task_id() -> UUID | None:
    with Session(engine) as session:
        statement = (
            select(ExtractedImageSemanticTask)
            .where(ExtractedImageSemanticTask.status == ExtractedImageSemanticTaskStatus.pending)
            .order_by(ExtractedImageSemanticTask.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        task = session.exec(statement).first()
        if task is None:
            return None

        now = utc_now()
        task.status = ExtractedImageSemanticTaskStatus.running
        task.started_at = now
        task.finished_at = None
        task.updated_at = now
        task.error_message = None
        task.attempt_count += 1
        session.add(task)
        session.commit()
        return task.id


def execute_extracted_image_semantic_task(
    task_id: UUID,
    *,
    client: LlmServiceClient | None = None,
    storage: MinioStorage | None = None,
) -> None:
    llm_client = client or get_llm_service_client()
    minio_storage = storage or get_minio_storage()

    with Session(engine) as session:
        task = session.get(ExtractedImageSemanticTask, task_id)
        if task is None:
            logger.warning("extracted image semantic task not found while executing, task_id=%s", task_id)
            return
        if task.status != ExtractedImageSemanticTaskStatus.running:
            logger.info(
                "Skip extracted image semantic task execution due to unexpected status, task_id=%s, status=%s",
                task_id,
                task.status,
            )
            return

        extracted_image = session.get(ExtractedImage, task.extracted_image_id)
        if extracted_image is None:
            _mark_task_failed(session, task=task, error_message="Extracted image not found")
            return

        execution_result = execute_extracted_image_semantic_recognition(
            extracted_image=extracted_image,
            storage=minio_storage,
            client=llm_client,
            request_id=task.request_id,
            target_model=task.target_model,
        )
        if not execution_result.succeeded:
            _mark_task_failed(
                session,
                task=task,
                error_message=execution_result.error_message or "Extracted image semantic recognition failed",
            )
            return

        now = utc_now()
        task.status = ExtractedImageSemanticTaskStatus.succeeded
        task.description = execution_result.description
        task.result_model = execution_result.result_model
        task.error_message = None
        task.finished_at = now
        task.updated_at = now
        session.add(task)

        if _should_update_extracted_image_semantic_snapshot(
            extracted_image,
            overwrite_existing_snapshot=task.overwrite_existing_snapshot,
        ):
            _update_extracted_image_semantic_snapshot(
                extracted_image,
                description=execution_result.description or "",
                result_model=execution_result.result_model,
                updated_at=now,
            )
            session.add(extracted_image)

        try:
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            logger.exception("Failed to mark extracted image semantic task succeeded, task_id=%s", task_id)
            try:
                _mark_task_failed(
                    session,
                    task=task,
                    error_message="Failed to update extracted image semantic task state",
                )
            except SQLAlchemyError:
                session.rollback()
                logger.exception(
                    "Failed to mark extracted image semantic task failed after success commit error, task_id=%s",
                    task_id,
                )


def process_one_pending_extracted_image_semantic_task(
    *,
    client: LlmServiceClient | None = None,
    storage: MinioStorage | None = None,
) -> bool:
    task_id = claim_next_pending_extracted_image_semantic_task_id()
    if task_id is None:
        return False

    execute_extracted_image_semantic_task(task_id, client=client, storage=storage)
    return True


class ExtractedImageSemanticTaskWorker:
    def __init__(self, *, poll_interval_seconds: float = 1.0) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._runner_task is not None and not self._runner_task.done():
            return

        self._stop_event = asyncio.Event()
        recovered = await asyncio.to_thread(recover_orphaned_extracted_image_semantic_tasks)
        if recovered > 0:
            logger.warning("Recovered orphaned running extracted image semantic tasks, count=%s", recovered)

        self._runner_task = asyncio.create_task(self._run_loop(), name="extracted-image-semantic-task-worker")
        logger.info("extracted image semantic task worker started")

    async def stop(self) -> None:
        runner_task = self._runner_task
        if runner_task is None:
            return

        self._stop_event.set()
        await runner_task
        self._runner_task = None
        logger.info("extracted image semantic task worker stopped")

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                processed = await asyncio.to_thread(process_one_pending_extracted_image_semantic_task)
            except Exception:
                logger.exception("Unexpected error while processing extracted image semantic task")
                processed = False

            if processed:
                continue

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                continue


@lru_cache(maxsize=1)
def get_extracted_image_semantic_task_worker() -> ExtractedImageSemanticTaskWorker:
    poll_interval_seconds = float(os.getenv("EXTRACTED_IMAGE_SEMANTIC_TASK_WORKER_POLL_INTERVAL_SECONDS", "1.0"))
    return ExtractedImageSemanticTaskWorker(poll_interval_seconds=poll_interval_seconds)


def is_extracted_image_semantic_task_worker_enabled() -> bool:
    value = os.getenv("EXTRACTED_IMAGE_SEMANTIC_TASK_WORKER_ENABLED")
    return _to_bool(value, default=True)