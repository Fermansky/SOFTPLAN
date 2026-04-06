"""抽取图片语义任务服务。

职责：
1. 创建、复用、领取与执行抽取图片语义任务。
2. 维护任务状态流转与图片语义快照更新规则。
3. 在 worker 重启后恢复孤儿任务，保证状态最终可解释。

说明：
- 本模块负责任务层状态机，不负责单次 LLM 识别细节；识别执行由 `extracted_image_semantic_service` 提供。
- 活动任务的 Dedup Reuse 由 `extracted_image_id + target_model_key + overwrite_existing_snapshot` 决定。
- 图片级语义快照默认只写一次，除非显式要求覆盖。
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
    get_extracted_image_semantic_prompt_snapshot,
    get_extracted_image_semantic_target_model_key,
    resolve_extracted_image_semantic_model,
)
from .llm_service import LlmServiceClient, get_llm_service_client
from .minio_storage import MinioStorage, get_minio_storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractedImageSemanticTaskSubmissionResult:
    """抽取图片语义任务提交结果。"""

    task: ExtractedImageSemanticTask
    reused: bool



def _to_bool(value: str | None, *, default: bool = False) -> bool:
    """将环境变量字符串解析为布尔值。"""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}



def _normalize_requested_model(requested_model: str | None) -> str | None:
    """规范化调用方传入的模型名。"""
    if requested_model is None:
        return None
    stripped = requested_model.strip()
    return stripped or None



def _has_semantic_snapshot(extracted_image: ExtractedImage) -> bool:
    """判断图片是否已有可复用的语义快照。"""
    description = extracted_image.semantic_description
    if description is None:
        return False
    return bool(description.strip())



def _should_update_extracted_image_semantic_snapshot(
    extracted_image: ExtractedImage,
    *,
    overwrite_existing_snapshot: bool,
) -> bool:
    """判断本次成功执行后是否应该刷新图片级语义快照。"""
    return overwrite_existing_snapshot or not _has_semantic_snapshot(extracted_image)



def get_active_extracted_image_semantic_task(
    session: Session,
    *,
    extracted_image_id: int,
    target_model_key: str,
    overwrite_existing_snapshot: bool,
) -> ExtractedImageSemanticTask | None:
    """查询与当前语义完全等价的活动任务。"""
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


# `use_target_model=True` 允许父级编排任务直接传入已解析好的目标模型，
# 避免在子任务创建时再次读取环境变量而产生模型漂移。
def create_or_reuse_extracted_image_semantic_task(
    session: Session,
    *,
    extracted_image: ExtractedImage,
    requested_model: str | None = None,
    target_model: str | None = None,
    use_target_model: bool = False,
    request_id: str | None = None,
    overwrite_existing_snapshot: bool = False,
) -> ExtractedImageSemanticTaskSubmissionResult:
    """创建或复用抽取图片语义任务。

    约束：
    - 任务复用只发生在活动态任务之间。
    - 去重语义取决于图片、目标模型语义与是否覆盖已有快照。

    副作用：
    - 会提交当前数据库事务。

    失败语义：
    - 并发冲突时优先回滚并复用获胜任务；若未查回获胜记录，则继续抛出原始异常。
    """
    normalized_requested_model = _normalize_requested_model(requested_model)
    normalized_target_model = _normalize_requested_model(target_model) if use_target_model else None
    if not use_target_model:
        # 常规路由入口在这里解析真正的执行模型。
        normalized_target_model = resolve_extracted_image_semantic_model(normalized_requested_model)
    target_model_key = get_extracted_image_semantic_target_model_key(normalized_target_model)
    prompt_path, prompt_hash = get_extracted_image_semantic_prompt_snapshot()

    # Reuse 维度明确后，重复提交相同语义任务不会创建多个活动 worker。
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
        target_model=normalized_target_model,
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
        # 让数据库唯一索引仲裁并发提交，失败后再尝试复用获胜记录。
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
    """按任务 id 查询抽取图片语义任务。"""
    statement = select(ExtractedImageSemanticTask).where(ExtractedImageSemanticTask.id == task_id)
    return session.exec(statement).first()



def get_latest_extracted_image_semantic_task_for_image(
    session: Session,
    *,
    extracted_image_id: int,
) -> ExtractedImageSemanticTask | None:
    """查询图片最近一次语义任务记录。"""
    statement = (
        select(ExtractedImageSemanticTask)
        .where(ExtractedImageSemanticTask.extracted_image_id == extracted_image_id)
        .order_by(ExtractedImageSemanticTask.created_at.desc())
    )
    return session.exec(statement).first()



def _mark_task_failed(session: Session, *, task: ExtractedImageSemanticTask, error_message: str) -> None:
    """将任务标记为失败并提交。"""
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
    """把任务执行结果写回图片级语义快照。"""
    extracted_image.semantic_description = description
    extracted_image.semantic_description_model = result_model
    extracted_image.semantic_description_updated_at = updated_at



def recover_orphaned_extracted_image_semantic_tasks() -> int:
    """恢复进程重启后遗留在 running 状态的孤儿任务。

    说明：
    - 旧 worker 已消失，继续保留 running 只会让任务长期不可解释，因此统一置为 failed。
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
    """领取一条待执行任务并切换到 running。

    说明：
    - 使用 `FOR UPDATE SKIP LOCKED`，避免多个 worker 抢到同一条任务。
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
    """执行单条抽取图片语义任务。

    流程：
    1. 校验任务与图片状态。
    2. 调用单次语义识别服务。
    3. 按覆盖策略更新任务结果与图片级语义快照。

    副作用：
    - 会访问 MinIO、调用 llm-service、提交数据库事务。

    失败语义：
    - 任一步骤失败都会把任务标记为 failed。
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

        # 任务历史总是保留，但图片级快照默认只在首次成功或显式覆盖时刷新。
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
    """处理一条待执行抽取图片语义任务。"""
    task_id = claim_next_pending_extracted_image_semantic_task_id()
    if task_id is None:
        return False

    execute_extracted_image_semantic_task(task_id, client=client, storage=storage)
    return True


class ExtractedImageSemanticTaskWorker:
    """抽取图片语义任务轮询 worker。"""

    def __init__(self, *, poll_interval_seconds: float = 1.0) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """启动 worker，并在正式轮询前恢复孤儿任务。"""
        if self._runner_task is not None and not self._runner_task.done():
            return

        self._stop_event = asyncio.Event()
        recovered = await asyncio.to_thread(recover_orphaned_extracted_image_semantic_tasks)
        if recovered > 0:
            logger.warning("Recovered orphaned running extracted image semantic tasks, count=%s", recovered)

        self._runner_task = asyncio.create_task(self._run_loop(), name="extracted-image-semantic-task-worker")
        logger.info("extracted image semantic task worker started")

    async def stop(self) -> None:
        """停止 worker，并等待当前轮询循环退出。"""
        runner_task = self._runner_task
        if runner_task is None:
            return

        self._stop_event.set()
        await runner_task
        self._runner_task = None
        logger.info("extracted image semantic task worker stopped")

    async def _run_loop(self) -> None:
        """持续轮询待执行任务，直到收到停止信号。"""
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
    """按环境变量构造抽取图片语义任务 worker 单例。"""
    poll_interval_seconds = float(os.getenv("EXTRACTED_IMAGE_SEMANTIC_TASK_WORKER_POLL_INTERVAL_SECONDS", "1.0"))
    return ExtractedImageSemanticTaskWorker(poll_interval_seconds=poll_interval_seconds)



def is_extracted_image_semantic_task_worker_enabled() -> bool:
    """读取抽取图片语义任务 worker 开关。"""
    value = os.getenv("EXTRACTED_IMAGE_SEMANTIC_TASK_WORKER_ENABLED")
    return _to_bool(value, default=True)
