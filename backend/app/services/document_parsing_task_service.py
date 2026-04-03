"""文档解析异步任务编排服务。

职责：
1. 创建/复用解析任务（防止同文档并发重复执行）。
2. 维护任务状态流转：pending -> running -> succeeded/failed。
3. 在后台 worker 中轮询执行任务并持久化结果。
4. 处理异常与重启恢复，避免任务悬挂在 running 状态。
"""

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
    """任务提交结果。

    - `reused=True` 表示命中了现有进行中任务。
    - `reused=False` 表示创建了新任务。
    """

    task: DocumentParsingTask
    reused: bool


def _to_bool(value: str | None, *, default: bool = False) -> bool:
    """将环境变量字符串解析为布尔值。"""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_active_document_parsing_task_for_document(session: Session, *, document_id: UUID) -> DocumentParsingTask | None:
    """获取某文档当前进行中的任务（pending/running）。"""
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
    """创建或复用解析任务。

    约束：
    - 同文档若已有进行中任务，直接复用。
    - 若并发创建触发唯一约束，回滚后再次查询并复用。
    """
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
    """按任务 ID 查询任务。"""
    statement = select(DocumentParsingTask).where(DocumentParsingTask.id == task_id)
    return session.exec(statement).first()


def get_latest_document_parsing_task_for_document_file(
    session: Session,
    *,
    document_id: UUID,
    file_id: UUID,
) -> DocumentParsingTask | None:
    """按 document_id + file_id 获取最新任务。"""
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
    """统一失败落库动作，避免各分支重复写状态字段。"""
    now = utc_now()
    task.status = DocumentParsingTaskStatus.failed
    task.error_message = error_message
    task.finished_at = now
    task.updated_at = now
    session.add(task)
    session.commit()


def recover_orphaned_document_parsing_tasks() -> int:
    """将历史遗留 running 任务标记为 failed。

    场景：进程异常退出后，running 任务不会自动回滚。
    处理：worker 启动时统一收敛，避免查询端永远看到 running。
    """
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
    """领取一个待处理任务并切换为 running。

    约束：
    - 使用 `FOR UPDATE SKIP LOCKED`，支持多 worker 并发无重复消费。
    """
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
    """执行单个解析任务。

    流程：
    1. 校验任务存在且处于 running。
    2. 调用下游解析服务。
    3. 持久化 extracted_images。
    4. 成功写回 markdown/image_hashes，失败写 failed。
    """
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
    """处理一个 pending 任务，返回是否实际处理了任务。"""
    task_id = claim_next_pending_document_parsing_task_id()
    if task_id is None:
        return False

    execute_document_parsing_task(task_id, client=client)
    return True


class DocumentParsingTaskWorker:
    """进程内任务 worker。"""

    def __init__(self, *, poll_interval_seconds: float = 1.0) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """启动 worker：先恢复孤儿任务，再进入轮询循环。"""
        if self._runner_task is not None and not self._runner_task.done():
            return

        self._stop_event = asyncio.Event()
        recovered = await asyncio.to_thread(recover_orphaned_document_parsing_tasks)
        if recovered > 0:
            logger.warning("Recovered orphaned running document parsing tasks, count=%s", recovered)

        self._runner_task = asyncio.create_task(self._run_loop(), name="document-parsing-task-worker")
        logger.info("document parsing task worker started")

    async def stop(self) -> None:
        """停止 worker 并等待当前循环优雅退出。"""
        runner_task = self._runner_task
        if runner_task is None:
            return

        self._stop_event.set()
        await runner_task
        self._runner_task = None
        logger.info("document parsing task worker stopped")

    async def _run_loop(self) -> None:
        """循环消费任务。

        设计点：
        - 有任务时立即继续下一轮，提高吞吐。
        - 无任务时按轮询间隔休眠，降低空转开销。
        """
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
    """构建 worker 单例。"""
    poll_interval_seconds = float(os.getenv("DOCUMENT_PARSING_TASK_WORKER_POLL_INTERVAL_SECONDS", "1.0"))
    return DocumentParsingTaskWorker(poll_interval_seconds=poll_interval_seconds)


def is_document_parsing_task_worker_enabled() -> bool:
    """读取 worker 开关。"""
    value = os.getenv("DOCUMENT_PARSING_TASK_WORKER_ENABLED")
    return _to_bool(value, default=True)

