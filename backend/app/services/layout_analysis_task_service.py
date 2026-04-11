"""版面分析任务服务。

职责：
1. 解析 layout analysis 的模型选择规则，并负责任务创建/复用。
2. 驱动任务状态在 pending、running、succeeded、failed 之间流转。
3. 协调 file-convert-service 解析结果、提取图片落库与文档解析任务同步。

说明：
- 当前仅支持 `marker` 作为版面分析模型，所有请求都会被收敛到该目标模型。
- 本模块既包含同步的任务执行逻辑，也包含后台 worker 的轮询编排逻辑。
- 对 file-convert-service 的实际 HTTP 适配由独立客户端负责，这里只关注任务编排。
"""
import asyncio
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath
from uuid import UUID

from minio.error import S3Error
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from ..database import engine
from ..models import LayoutAnalysisTask, LayoutAnalysisTaskStatus
from ..models.common import utc_now
from .extracted_image_persistence_service import ExtractedImagePersistenceError, persist_extracted_images
from .file_convert_service import FileConvertServiceClient, UploadedImageMetadata, get_file_convert_service_client
from .minio_storage import MinioStorage, get_minio_storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LayoutAnalysisTaskSubmissionResult:
    """版面分析任务提交结果。"""

    task: LayoutAnalysisTask
    reused: bool


@dataclass(frozen=True)
class LayoutAnalysisModelSelection:
    """版面分析模型选择结果。"""

    requested_model: str | None
    target_model: str
    model_key: str


class UnsupportedLayoutAnalysisModelError(ValueError):
    """请求了当前后端不支持的版面分析模型。"""

    pass



def _to_bool(value: str | None, *, default: bool = False) -> bool:
    """将环境变量风格的字符串解析为布尔值。"""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}



def _normalize_optional_model(value: str | None) -> str | None:
    """标准化可选模型名，去掉空白并把空字符串转为 `None`。"""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None



def resolve_layout_analysis_model_selection(requested_model: str | None) -> LayoutAnalysisModelSelection:
    """解析版面分析模型选择。

    说明：
    - 当前所有合法请求最终都会收敛为 `marker`。
    - 非法模型名直接抛出异常，交由上层转换为 422。
    """
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
    """获取文档当前仍在进行中的版面分析任务。"""
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
    """获取同文件最近一次成功完成的版面分析结果。"""
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
    """创建或复用版面分析任务。

    复用语义：
    - 优先复用同文档下仍处于 `pending/running` 的活跃任务。
    - 未强制重跑时，可复用同文件最近一次成功结果。
    """
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
    """按任务 ID 查询版面分析任务。"""
    statement = select(LayoutAnalysisTask).where(LayoutAnalysisTask.id == task_id)
    return session.exec(statement).first()



def get_latest_layout_analysis_task_for_document_file(
    session: Session,
    *,
    document_id: UUID,
    file_id: UUID,
) -> LayoutAnalysisTask | None:
    """获取文档当前文件最近一次版面分析任务。"""
    statement = (
        select(LayoutAnalysisTask)
        .where(LayoutAnalysisTask.document_id == document_id, LayoutAnalysisTask.file_id == file_id)
        .order_by(LayoutAnalysisTask.created_at.desc())
    )
    return session.exec(statement).first()



def _mark_task_failed(session: Session, *, task: LayoutAnalysisTask, error_message: str) -> None:
    """将版面分析任务标记为失败并立即提交。"""
    now = utc_now()
    task.status = LayoutAnalysisTaskStatus.failed
    task.error_message = error_message
    task.finished_at = now
    task.updated_at = now
    session.add(task)
    session.commit()



def _synchronize_document_parsing_tasks(layout_task_id: UUID) -> None:
    """同步受某个版面分析任务影响的聚合父任务。"""
    try:
        from .document_parsing_task_service import process_document_parsing_tasks_for_layout_task

        process_document_parsing_tasks_for_layout_task(layout_task_id)
    except Exception:
        logger.exception(
            "Failed to synchronize document parsing tasks after layout analysis task update, task_id=%s",
            layout_task_id,
        )



def recover_orphaned_layout_analysis_tasks() -> int:
    """将 worker 重启前遗留的 running 任务恢复为 failed。

    恢复后会同步关联的 `DocumentParsingTask`，避免父任务长期停留在旧状态。
    """
    with Session(engine) as session:
        statement = select(LayoutAnalysisTask).where(LayoutAnalysisTask.status == LayoutAnalysisTaskStatus.running)
        running_tasks = list(session.exec(statement).all())
        if not running_tasks:
            return 0

        now = utc_now()
        task_ids: list[UUID] = []
        for task in running_tasks:
            task.status = LayoutAnalysisTaskStatus.failed
            task.error_message = "Worker restarted before completion"
            task.finished_at = now
            task.updated_at = now
            session.add(task)
            task_ids.append(task.id)

        session.commit()

    for task_id in task_ids:
        _synchronize_document_parsing_tasks(task_id)
    return len(task_ids)



def claim_next_pending_layout_analysis_task_id() -> UUID | None:
    """抢占一个最早创建的 pending 版面分析任务。"""
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
    """判断历史版面分析任务是否可作为结果复用源。"""
    if task is None or task.status != LayoutAnalysisTaskStatus.succeeded:
        return False
    return task.markdown is not None and task.image_hashes is not None



def _resolve_pdf_filename(storage_key: str) -> str:
    """Map a stored object key to a safe PDF filename for multipart upload."""
    filename = PurePosixPath(storage_key).name.strip() or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        return "document.pdf"
    return filename


def _upload_inline_images_to_storage(storage: MinioStorage, parsing_result) -> list[UploadedImageMetadata]:
    """Upload inline images returned by file-convert-service to backend MinIO."""
    uploaded_images: list[UploadedImageMetadata] = []
    for item in parsing_result.inline_images:
        storage_ref = storage.upload_image_bytes(item.payload, content_type=item.content_type)
        stored_extension = PurePosixPath(storage_ref.storage_key).suffix.lower() or None
        uploaded_images.append(
            UploadedImageMetadata(
                source_key=item.source_key,
                file_hash=item.file_hash,
                storage_bucket=storage_ref.bucket,
                storage_key=storage_ref.storage_key,
                file_size=item.file_size,
                content_type=item.content_type,
                extension=stored_extension,
                width=item.width,
                height=item.height,
            )
        )
    return uploaded_images


def execute_layout_analysis_task(task_id: UUID, *, client: FileConvertServiceClient | None = None) -> None:
    """执行单个已被 claim 为 running 的版面分析任务。

    执行语义：
    - 如存在可复用的历史成功结果，则直接复用其 markdown 与图片映射。
    - 否则调用 file-convert-service 进行实时解析并持久化抽取图片。
    - 无论成功还是失败，都会回灌关联的 `DocumentParsingTask` 聚合状态。
    """
    file_convert_client = client or get_file_convert_service_client()
    storage = get_minio_storage()

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
            try:
                pdf_payload = storage.download_bytes(task.storage_key, bucket=task.storage_bucket)
            except S3Error as exc:
                logger.warning(
                    "layout analysis task failed to download source PDF, task_id=%s, storage_key=%s, error=%s",
                    task_id,
                    task.storage_key,
                    exc.code,
                )
                _mark_task_failed(session, task=task, error_message=f"Source PDF download failed: {exc.code}")
                _synchronize_document_parsing_tasks(task.id)
                return

            parsing_result, error = file_convert_client.convert_pdf_to_markdown_from_file(
                filename=_resolve_pdf_filename(task.storage_key),
                payload=pdf_payload,
                task_id=str(task.id),
                model=task.target_layout_model,
            )
            if error is not None or parsing_result is None:
                logger.warning("layout analysis task failed on file-convert-service, task_id=%s, error=%s", task_id, error)
                _mark_task_failed(session, task=task, error_message=error or "file-convert-service parsing failed")
                _synchronize_document_parsing_tasks(task.id)
                return

            markdown = parsing_result.markdown
            image_hashes = dict(parsing_result.image_hashes or {})
            try:
                uploaded_images = _upload_inline_images_to_storage(storage, parsing_result)
                persist_extracted_images(session, uploaded_images=uploaded_images)
            except (ExtractedImagePersistenceError, SQLAlchemyError, S3Error):
                logger.exception("Failed to persist extracted images for layout analysis task, task_id=%s", task_id)
                _mark_task_failed(session, task=task, error_message="Failed to persist extracted images")
                _synchronize_document_parsing_tasks(task.id)
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
            _synchronize_document_parsing_tasks(task.id)
            return

    _synchronize_document_parsing_tasks(task_id)



def process_one_pending_layout_analysis_task(*, client: FileConvertServiceClient | None = None) -> bool:
    """尝试处理一个待执行的版面分析任务。"""
    task_id = claim_next_pending_layout_analysis_task_id()
    if task_id is None:
        return False

    execute_layout_analysis_task(task_id, client=client)
    return True


class LayoutAnalysisTaskWorker:
    """版面分析任务轮询 worker。"""

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
        recovered = await asyncio.to_thread(recover_orphaned_layout_analysis_tasks)
        if recovered > 0:
            logger.warning("Recovered orphaned running layout analysis tasks, count=%s", recovered)

        self._runner_task = asyncio.create_task(self._run_loop(), name="layout-analysis-task-worker")
        logger.info("layout analysis task worker started")

    async def stop(self) -> None:
        """停止 worker 轮询循环。"""
        runner_task = self._runner_task
        if runner_task is None:
            return

        self._stop_event.set()
        await runner_task
        self._runner_task = None
        logger.info("layout analysis task worker stopped")

    async def _run_loop(self) -> None:
        """持续轮询并处理待执行任务，直到收到停止信号。"""
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
    """获取版面分析任务 worker 单例。"""
    poll_interval_seconds = float(
        os.getenv(
            "LAYOUT_ANALYSIS_TASK_WORKER_POLL_INTERVAL_SECONDS",
            os.getenv("DOCUMENT_PARSING_TASK_WORKER_POLL_INTERVAL_SECONDS", "1.0"),
        )
    )
    return LayoutAnalysisTaskWorker(poll_interval_seconds=poll_interval_seconds)



def is_layout_analysis_task_worker_enabled() -> bool:
    """判断是否启用版面分析任务 worker。"""
    value = os.getenv("LAYOUT_ANALYSIS_TASK_WORKER_ENABLED")
    if value is None:
        value = os.getenv("DOCUMENT_PARSING_TASK_WORKER_ENABLED")
    return _to_bool(value, default=True)



