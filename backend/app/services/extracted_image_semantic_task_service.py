"""提取图片语义识别异步任务编排服务。

职责：
1. 创建或复用图片语义识别任务，避免同图同模型并发重复执行。
2. 维护任务状态流转：pending -> running -> succeeded/failed。
3. 在进程内 worker 中轮询执行任务，并持久化执行结果。
4. 在 worker 重启后收敛遗留 running 任务，避免任务长期悬挂。

说明：
- 本模块负责任务层编排与落库，不负责单次识别调用细节。
- 单次识别逻辑由 `extracted_image_semantic_service` 提供。
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
from ..models import ExtractedImage, ExtractedImageSemanticTask, ExtractedImageSemanticTaskStatus
from ..models.common import utc_now
from .extracted_image_semantic_service import (
    execute_extracted_image_semantic_recognition,
    get_extracted_image_semantic_model,
    get_extracted_image_semantic_prompt_snapshot,
    get_extracted_image_semantic_target_model_key,
    resolve_extracted_image_semantic_model,
)
from .llm_service import LlmServiceClient, get_llm_service_client
from .minio_storage import MinioStorage, get_minio_storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractedImageSemanticTaskSubmissionResult:
    """任务提交结果。

    - `reused=True` 表示命中了同图同模型的进行中任务。
    - `reused=False` 表示创建了新的执行任务。
    """

    task: ExtractedImageSemanticTask
    reused: bool


def _to_bool(value: str | None, *, default: bool = False) -> bool:
    """将环境变量字符串解析为布尔值。"""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_requested_model(requested_model: str | None) -> str | None:
    """规范化请求级模型名，空字符串按未传处理。"""
    if requested_model is None:
        return None
    stripped = requested_model.strip()
    return stripped or None


def get_active_extracted_image_semantic_task(
    session: Session,
    *,
    extracted_image_id: int,
    target_model_key: str,
) -> ExtractedImageSemanticTask | None:
    """查询同图同模型当前可复用的活动任务。

    约束：
    - 仅 `pending/running` 状态可复用。
    - 复用键由 `extracted_image_id + target_model_key` 组成。
    """
    statement = (
        select(ExtractedImageSemanticTask)
        .where(
            ExtractedImageSemanticTask.extracted_image_id == extracted_image_id,
            ExtractedImageSemanticTask.target_model_key == target_model_key,
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
) -> ExtractedImageSemanticTaskSubmissionResult:
    """创建或复用图片语义识别任务。

    约束：
    - 同图同模型在 `pending/running` 状态下只保留一个活动任务。
    - prompt 路径和哈希在创建时快照，便于后续排查任务使用的提示词版本。

    副作用：
    - 可能提交一次数据库事务创建任务记录。

    失败语义：
    - 若并发创建命中唯一约束，回滚后再次查询并复用已存在任务。
    """
    normalized_requested_model = _normalize_requested_model(requested_model)
    target_model = resolve_extracted_image_semantic_model(normalized_requested_model)
    target_model_key = get_extracted_image_semantic_target_model_key(target_model)
    prompt_path, prompt_hash = get_extracted_image_semantic_prompt_snapshot()

    existing = get_active_extracted_image_semantic_task(
        session,
        extracted_image_id=extracted_image.id or 0,
        target_model_key=target_model_key,
    )
    if existing is not None:
        return ExtractedImageSemanticTaskSubmissionResult(task=existing, reused=True)

    task = ExtractedImageSemanticTask(
        extracted_image_id=extracted_image.id or 0,
        status=ExtractedImageSemanticTaskStatus.pending,
        requested_model=normalized_requested_model,
        target_model=target_model,
        target_model_key=target_model_key,
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
        # 并发提交同图同模型任务时，唯一约束是最终兜底；回滚后复用已存在任务即可。
        session.rollback()
        existing_after_conflict = get_active_extracted_image_semantic_task(
            session,
            extracted_image_id=extracted_image.id or 0,
            target_model_key=target_model_key,
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
    """按任务 ID 查询语义识别任务。"""
    statement = select(ExtractedImageSemanticTask).where(ExtractedImageSemanticTask.id == task_id)
    return session.exec(statement).first()


def get_latest_extracted_image_semantic_task_for_image(
    session: Session,
    *,
    extracted_image_id: int,
) -> ExtractedImageSemanticTask | None:
    """查询某张图片最新一次语义识别任务。"""
    statement = (
        select(ExtractedImageSemanticTask)
        .where(ExtractedImageSemanticTask.extracted_image_id == extracted_image_id)
        .order_by(ExtractedImageSemanticTask.created_at.desc())
    )
    return session.exec(statement).first()


def _mark_task_failed(session: Session, *, task: ExtractedImageSemanticTask, error_message: str) -> None:
    """统一写入 failed 状态，避免失败分支重复维护状态字段。"""
    now = utc_now()
    task.status = ExtractedImageSemanticTaskStatus.failed
    task.error_message = error_message
    task.finished_at = now
    task.updated_at = now
    session.add(task)
    session.commit()


def recover_orphaned_extracted_image_semantic_tasks() -> int:
    """将历史遗留的 running 任务统一收敛为 failed。

    场景：
    - worker 异常退出后，数据库中的 running 状态不会自动恢复。

    处理：
    - 在 worker 启动时执行一次收敛，确保查询侧不会永久看到 running。
    """
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
    """领取一个待处理任务并切换到 running。

    约束：
    - 使用 `FOR UPDATE SKIP LOCKED` 支持多 worker 并发无重复消费。
    - 领取成功后立即累加 `attempt_count`，保证执行次数可审计。
    """
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
        # 领取后立即切到 running，避免同一任务被其他 worker 重复消费。
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
    """执行单个图片语义识别任务。

    流程：
    1. 校验任务仍处于 running。
    2. 读取关联图片记录。
    3. 调用单次识别服务。
    4. 成功写回描述和 result_model，失败写回 failed。

    失败语义：
    - 任何业务失败都尽量落为任务状态，而不是向 worker 循环抛出异常。
    """
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
        try:
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            logger.exception("Failed to mark extracted image semantic task succeeded, task_id=%s", task_id)
            try:
                # 成功结果回写失败时，退化为 failed，避免任务永久停留在 running。
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
    """处理一个 pending 任务，并返回是否实际执行了任务。"""
    task_id = claim_next_pending_extracted_image_semantic_task_id()
    if task_id is None:
        return False

    execute_extracted_image_semantic_task(task_id, client=client, storage=storage)
    return True


class ExtractedImageSemanticTaskWorker:
    """进程内图片语义识别任务 worker。"""

    def __init__(self, *, poll_interval_seconds: float = 1.0) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """启动 worker：先恢复孤儿任务，再进入轮询循环。"""
        if self._runner_task is not None and not self._runner_task.done():
            return

        self._stop_event = asyncio.Event()
        recovered = await asyncio.to_thread(recover_orphaned_extracted_image_semantic_tasks)
        if recovered > 0:
            logger.warning("Recovered orphaned running extracted image semantic tasks, count=%s", recovered)

        self._runner_task = asyncio.create_task(self._run_loop(), name="extracted-image-semantic-task-worker")
        logger.info("extracted image semantic task worker started")

    async def stop(self) -> None:
        """停止 worker 并等待当前循环优雅退出。"""
        runner_task = self._runner_task
        if runner_task is None:
            return

        self._stop_event.set()
        await runner_task
        self._runner_task = None
        logger.info("extracted image semantic task worker stopped")

    async def _run_loop(self) -> None:
        """轮询消费任务。

        设计点：
        - 有任务时立刻继续下一轮，优先清空积压。
        - 无任务时按轮询间隔休眠，降低空转开销。
        """
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
    """按环境变量构造图片语义识别任务 worker 单例。"""
    poll_interval_seconds = float(os.getenv("EXTRACTED_IMAGE_SEMANTIC_TASK_WORKER_POLL_INTERVAL_SECONDS", "1.0"))
    return ExtractedImageSemanticTaskWorker(poll_interval_seconds=poll_interval_seconds)


def is_extracted_image_semantic_task_worker_enabled() -> bool:
    """读取图片语义识别任务 worker 开关。"""
    value = os.getenv("EXTRACTED_IMAGE_SEMANTIC_TASK_WORKER_ENABLED")
    return _to_bool(value, default=True)


