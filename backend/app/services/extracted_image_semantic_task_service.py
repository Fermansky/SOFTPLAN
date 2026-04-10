"""抽取图片语义任务服务。

职责：
1. 解析图片语义识别的模型选择规则，并负责任务创建/复用。
2. 驱动任务状态在 pending、running、succeeded、failed 之间流转。
3. 协调语义快照落库、旧字段兼容更新与文档解析任务同步。

说明：
- 本模块既包含同步的任务执行逻辑，也包含后台 worker 的轮询编排逻辑。
- 语义结果会优先以模型作用域快照形式持久化，兼容字段仅作为历史兼容输出。
- LLM 调用与对象读取分别由独立 service/client 负责，这里只关注任务编排。
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
from ..models import (
    ExtractedImage,
    ExtractedImageSemanticSnapshot,
    ExtractedImageSemanticTask,
    ExtractedImageSemanticTaskStatus,
)
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
    """将环境变量风格的字符串解析为布尔值。"""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}



def _normalize_requested_model(requested_model: str | None) -> str | None:
    """标准化可选模型名，去掉空白并把空字符串转为 `None`。"""
    if requested_model is None:
        return None
    stripped = requested_model.strip()
    return stripped or None



def _has_legacy_semantic_snapshot(extracted_image: ExtractedImage) -> bool:
    """判断旧版兼容字段上是否已有可用语义结果。"""
    description = extracted_image.semantic_description
    if description is None:
        return False
    return bool(description.strip())



def _should_update_legacy_extracted_image_semantic_snapshot(
    extracted_image: ExtractedImage,
    *,
    overwrite_existing_snapshot: bool,
) -> bool:
    """判断本次执行是否应回填旧版兼容字段。"""
    return overwrite_existing_snapshot or not _has_legacy_semantic_snapshot(extracted_image)



def get_active_extracted_image_semantic_task(
    session: Session,
    *,
    extracted_image_id: int,
    target_model_key: str,
    overwrite_existing_snapshot: bool,
) -> ExtractedImageSemanticTask | None:
    """获取图片当前仍在进行中的语义任务。

    说明：
    - `overwrite_existing_snapshot` 参与任务唯一性判断，避免覆盖策略不同的任务互相复用。
    """
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



def get_extracted_image_semantic_snapshot(
    session: Session,
    *,
    extracted_image_id: int,
    target_model_key: str,
) -> ExtractedImageSemanticSnapshot | None:
    """获取指定图片在目标模型作用域下最近的语义快照。"""
    statement = (
        select(ExtractedImageSemanticSnapshot)
        .where(
            ExtractedImageSemanticSnapshot.extracted_image_id == extracted_image_id,
            ExtractedImageSemanticSnapshot.target_model_key == target_model_key,
        )
        .order_by(ExtractedImageSemanticSnapshot.updated_at.desc())
    )
    return session.exec(statement).first()


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

    说明：
    - `use_target_model=True` 允许父级编排任务直接传入已解析好的目标模型，
      避免子任务创建时再次读取环境变量而产生模型漂移。
    - 优先复用同图片、同目标模型、同覆盖策略下仍处于 `pending/running` 的活跃任务。
    """
    normalized_requested_model = _normalize_requested_model(requested_model)
    normalized_target_model = _normalize_requested_model(target_model) if use_target_model else None
    if not use_target_model:
        normalized_target_model = resolve_extracted_image_semantic_model(normalized_requested_model)
    target_model_key = get_extracted_image_semantic_target_model_key(normalized_target_model)
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
    """按任务 ID 查询抽取图片语义任务。"""
    statement = select(ExtractedImageSemanticTask).where(ExtractedImageSemanticTask.id == task_id)
    return session.exec(statement).first()



def get_latest_extracted_image_semantic_task_for_image(
    session: Session,
    *,
    extracted_image_id: int,
) -> ExtractedImageSemanticTask | None:
    """获取单张图片最近一次语义任务。"""
    statement = (
        select(ExtractedImageSemanticTask)
        .where(ExtractedImageSemanticTask.extracted_image_id == extracted_image_id)
        .order_by(ExtractedImageSemanticTask.created_at.desc())
    )
    return session.exec(statement).first()



def _mark_task_failed(session: Session, *, task: ExtractedImageSemanticTask, error_message: str) -> None:
    """将图片语义任务标记为失败并立即提交。"""
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
    """更新旧版兼容字段上的图片语义结果。"""
    extracted_image.semantic_description = description
    extracted_image.semantic_description_model = result_model
    extracted_image.semantic_description_updated_at = updated_at



def _upsert_model_scoped_semantic_snapshot(
    session: Session,
    *,
    extracted_image_id: int,
    target_model_key: str,
    description: str,
    result_model: str | None,
    source_task_id: UUID,
    updated_at,
) -> None:
    """写入或更新模型作用域下的语义快照。"""
    snapshot = get_extracted_image_semantic_snapshot(
        session,
        extracted_image_id=extracted_image_id,
        target_model_key=target_model_key,
    )
    if snapshot is None:
        snapshot = ExtractedImageSemanticSnapshot(
            extracted_image_id=extracted_image_id,
            target_model_key=target_model_key,
            description=description,
            result_model=result_model,
            source_task_id=source_task_id,
        )
    else:
        snapshot.description = description
        snapshot.result_model = result_model
        snapshot.source_task_id = source_task_id
        snapshot.updated_at = updated_at
    session.add(snapshot)



def recover_orphaned_extracted_image_semantic_tasks() -> int:
    """将 worker 重启前遗留的 running 图片语义任务恢复为 failed。

    恢复后会同步关联的 `DocumentParsingTask`，避免父任务状态残留。
    """
    with Session(engine) as session:
        statement = select(ExtractedImageSemanticTask).where(
            ExtractedImageSemanticTask.status == ExtractedImageSemanticTaskStatus.running
        )
        running_tasks = list(session.exec(statement).all())
        if not running_tasks:
            return 0

        now = utc_now()
        task_ids: list[UUID] = []
        for task in running_tasks:
            task.status = ExtractedImageSemanticTaskStatus.failed
            task.error_message = "Worker restarted before completion"
            task.finished_at = now
            task.updated_at = now
            session.add(task)
            task_ids.append(task.id)

        session.commit()

    for task_id in task_ids:
        _synchronize_document_parsing_tasks(task_id)
    return len(task_ids)



def claim_next_pending_extracted_image_semantic_task_id() -> UUID | None:
    """抢占一个最早创建的 pending 图片语义任务。"""
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



def _synchronize_document_parsing_tasks(task_id: UUID) -> None:
    """同步受某个图片语义任务影响的聚合父任务。"""
    try:
        from .document_parsing_task_service import process_document_parsing_tasks_for_semantic_task

        process_document_parsing_tasks_for_semantic_task(task_id)
    except Exception:
        logger.exception("Failed to synchronize document parsing tasks after semantic task completion, task_id=%s", task_id)



def execute_extracted_image_semantic_task(
    task_id: UUID,
    *,
    client: LlmServiceClient | None = None,
    storage: MinioStorage | None = None,
) -> None:
    """执行单个已被 claim 为 running 的图片语义任务。

    执行语义：
    - 成功时写入模型作用域快照，并在需要时回填旧版兼容字段。
    - 失败时只更新任务状态，不覆盖既有成功快照。
    - 无论成功还是失败，都会回灌关联的 `DocumentParsingTask` 聚合状态。
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
            _synchronize_document_parsing_tasks(task.id)
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
            _synchronize_document_parsing_tasks(task.id)
            return

        now = utc_now()
        task.status = ExtractedImageSemanticTaskStatus.succeeded
        task.description = execution_result.description
        task.result_model = execution_result.result_model
        task.error_message = None
        task.finished_at = now
        task.updated_at = now
        session.add(task)

        _upsert_model_scoped_semantic_snapshot(
            session,
            extracted_image_id=extracted_image.id or 0,
            target_model_key=task.target_model_key,
            description=execution_result.description or "",
            result_model=execution_result.result_model,
            source_task_id=task.id,
            updated_at=now,
        )

        if _should_update_legacy_extracted_image_semantic_snapshot(
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
                return

    _synchronize_document_parsing_tasks(task_id)



def process_one_pending_extracted_image_semantic_task(
    *,
    client: LlmServiceClient | None = None,
    storage: MinioStorage | None = None,
) -> bool:
    """尝试处理一个待执行的图片语义任务。"""
    task_id = claim_next_pending_extracted_image_semantic_task_id()
    if task_id is None:
        return False

    execute_extracted_image_semantic_task(task_id, client=client, storage=storage)
    return True


class ExtractedImageSemanticTaskWorker:
    """抽取图片语义任务轮询 worker。"""

    def __init__(self, *, poll_interval_seconds: float = 1.0) -> None:
        """初始化 worker 轮询参数与停止信号。"""
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """启动 worker，并在启动时恢复 orphaned running 任务。"""
        if self._runner_task is not None and not self._runner_task.done():
            return

        self._stop_event = asyncio.Event()
        recovered = await asyncio.to_thread(recover_orphaned_extracted_image_semantic_tasks)
        if recovered > 0:
            logger.warning("Recovered orphaned running extracted image semantic tasks, count=%s", recovered)

        self._runner_task = asyncio.create_task(self._run_loop(), name="extracted-image-semantic-task-worker")
        logger.info("extracted image semantic task worker started")

    async def stop(self) -> None:
        """停止 worker 轮询循环。"""
        runner_task = self._runner_task
        if runner_task is None:
            return

        self._stop_event.set()
        await runner_task
        self._runner_task = None
        logger.info("extracted image semantic task worker stopped")

    async def _run_loop(self) -> None:
        """持续轮询并处理待执行任务，直到收到停止信号。"""
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
    """获取抽取图片语义任务 worker 单例。"""
    poll_interval_seconds = float(os.getenv("EXTRACTED_IMAGE_SEMANTIC_TASK_WORKER_POLL_INTERVAL_SECONDS", "1.0"))
    return ExtractedImageSemanticTaskWorker(poll_interval_seconds=poll_interval_seconds)



def is_extracted_image_semantic_task_worker_enabled() -> bool:
    """判断是否启用抽取图片语义任务 worker。"""
    value = os.getenv("EXTRACTED_IMAGE_SEMANTIC_TASK_WORKER_ENABLED")
    return _to_bool(value, default=True)
