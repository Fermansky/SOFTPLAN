"""文档解析任务编排服务。

职责：
1. 创建、复用、领取与执行文档解析任务。
2. 负责 PDF 转 markdown、抽取图片持久化、图片语义任务分发的主流程编排。
3. 在 worker 重启后恢复孤儿任务，保证任务状态最终可解释。

说明：
- 本模块使用 `document_id + pdf_model_key + image_model_key` 作为活动任务的 Dedup Reuse 语义。
- 顶层任务成功的定义是：markdown 生成成功，且图片语义任务已完成派发或跳过记录；不等待子任务真正执行完毕。
- 图片语义子任务使用文档任务创建时已解析好的目标模型，避免运行时环境变化导致语义漂移。
"""

import asyncio
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from ..database import engine
from ..models import (
    DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY,
    DEFAULT_DOCUMENT_PARSING_PDF_MODEL,
    DocumentParsingTask,
    DocumentParsingTaskStatus,
    ExtractedImage,
)
from ..models.common import utc_now
from .extracted_image_persistence_service import ExtractedImagePersistenceError, persist_extracted_images
from .extracted_image_semantic_service import (
    get_extracted_image_semantic_target_model_key,
    resolve_extracted_image_semantic_model,
)
from .extracted_image_semantic_task_service import create_or_reuse_extracted_image_semantic_task
from .file_convert_service import FileConvertServiceClient, UploadedImageMetadata, get_file_convert_service_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentParsingTaskSubmissionResult:
    """文档解析任务提交结果。"""

    task: DocumentParsingTask
    reused: bool


@dataclass(frozen=True)
class DocumentParsingModelSelection:
    """文档解析任务中单个模型维度的归一化结果。"""

    requested_model: str | None
    target_model: str | None
    model_key: str


@dataclass(frozen=True)
class DocumentParsingImageRef:
    """用于图片语义派发的轻量图片引用。"""

    source_key: str
    file_hash: str


class DocumentParsingSemanticDispatchError(RuntimeError):
    """图片语义任务派发阶段失败。"""


class UnsupportedDocumentParsingPdfModelError(ValueError):
    """请求了当前不支持的 PDF 解析模型。"""



def _to_bool(value: str | None, *, default: bool = False) -> bool:
    """将环境变量字符串解析为布尔值。"""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}



def _normalize_optional_model(value: str | None) -> str | None:
    """规范化可选模型名，空白字符串视为未指定。"""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None



def resolve_document_parsing_pdf_model_selection(requested_model: str | None) -> DocumentParsingModelSelection:
    """解析文档解析任务的 PDF 模型语义。

    约束：
    - 当前 backend 仅支持 `marker`。
    - 未显式指定时仍会归一化到 `marker`，确保任务去重语义稳定。

    失败语义：
    - 请求了非 `marker` 模型时抛出 `UnsupportedDocumentParsingPdfModelError`。
    """
    normalized_requested_model = _normalize_optional_model(requested_model)
    if normalized_requested_model is None:
        return DocumentParsingModelSelection(
            requested_model=None,
            target_model=DEFAULT_DOCUMENT_PARSING_PDF_MODEL,
            model_key=DEFAULT_DOCUMENT_PARSING_PDF_MODEL,
        )

    normalized_target_model = normalized_requested_model.lower()
    if normalized_target_model != DEFAULT_DOCUMENT_PARSING_PDF_MODEL:
        raise UnsupportedDocumentParsingPdfModelError(
            f"Unsupported pdf_model: {normalized_requested_model}. Only '{DEFAULT_DOCUMENT_PARSING_PDF_MODEL}' is supported"
        )

    return DocumentParsingModelSelection(
        requested_model=normalized_requested_model,
        target_model=DEFAULT_DOCUMENT_PARSING_PDF_MODEL,
        model_key=DEFAULT_DOCUMENT_PARSING_PDF_MODEL,
    )



def resolve_document_parsing_image_model_selection(requested_model: str | None) -> DocumentParsingModelSelection:
    """解析文档解析任务的图片语义模型语义。

    说明：
    - 这里的 `target_model` 是未来真正传给图片语义子任务的执行模型。
    - `model_key` 始终非空，用于活动任务去重，即使实际执行依赖 llm-service 默认模型。
    """
    normalized_requested_model = _normalize_optional_model(requested_model)
    target_model = resolve_extracted_image_semantic_model(normalized_requested_model)
    model_key = get_extracted_image_semantic_target_model_key(target_model)
    return DocumentParsingModelSelection(
        requested_model=normalized_requested_model,
        target_model=target_model,
        model_key=model_key,
    )



def _has_semantic_snapshot(extracted_image: ExtractedImage) -> bool:
    """判断图片是否已有可复用的语义快照。"""
    description = extracted_image.semantic_description
    if description is None:
        return False
    return bool(description.strip())



def _load_extracted_images_by_hash(
    session: Session,
    *,
    file_hashes: Sequence[str],
) -> dict[str, ExtractedImage]:
    """按 file_hash 批量回查抽取图片记录。"""
    normalized_hashes = [file_hash for file_hash in file_hashes if file_hash]
    if not normalized_hashes:
        return {}

    statement = select(ExtractedImage).where(ExtractedImage.file_hash.in_(normalized_hashes))
    extracted_images = list(session.exec(statement).all())
    return {image.file_hash: image for image in extracted_images}



def _build_image_refs_from_uploaded_images(uploaded_images: Sequence[UploadedImageMetadata]) -> list[DocumentParsingImageRef]:
    """将下游上传结果转换为统一图片引用。"""
    return [DocumentParsingImageRef(source_key=image.source_key, file_hash=image.file_hash) for image in uploaded_images if image.file_hash]



def _build_image_refs_from_image_hashes(image_hashes: Mapping[str, str] | None) -> list[DocumentParsingImageRef]:
    """将缓存的 image_hashes 映射转换为统一图片引用。"""
    if not image_hashes:
        return []
    return [
        DocumentParsingImageRef(source_key=source_key, file_hash=file_hash)
        for source_key, file_hash in image_hashes.items()
        if source_key and file_hash
    ]



def _task_has_complete_semantic_result(task: DocumentParsingTask) -> bool:
    """判断任务是否已具备可直接复用的完整文档解析结果。"""
    if task.status != DocumentParsingTaskStatus.succeeded:
        return False

    image_hashes = task.image_hashes or {}
    if not image_hashes:
        return True

    if not task.semantic_dispatches:
        return False

    dispatched_source_keys = {
        str(dispatch.get("source_key"))
        for dispatch in task.semantic_dispatches
        if isinstance(dispatch, dict) and dispatch.get("source_key")
    }
    return set(image_hashes.keys()).issubset(dispatched_source_keys)



def _is_reusable_pdf_result_source(task: DocumentParsingTask | None) -> bool:
    """判断任务是否可作为 PDF 结果缓存来源。"""
    if task is None or task.status != DocumentParsingTaskStatus.succeeded:
        return False
    return task.markdown is not None and task.image_hashes is not None



def _dispatch_semantic_tasks_for_image_refs(
    session: Session,
    *,
    image_refs: Sequence[DocumentParsingImageRef],
    request_id: str,
    requested_image_model: str | None,
    target_image_model: str | None,
) -> list[dict[str, object | None]]:
    """为给定图片引用分发语义任务。"""
    if not image_refs:
        return []

    images_by_hash = _load_extracted_images_by_hash(
        session,
        file_hashes=[image_ref.file_hash for image_ref in image_refs],
    )

    semantic_dispatches: list[dict[str, object | None]] = []
    for image_ref in image_refs:
        extracted_image = images_by_hash.get(image_ref.file_hash)
        if extracted_image is None or extracted_image.id is None:
            raise DocumentParsingSemanticDispatchError(
                f"Extracted image not found after persistence for hash={image_ref.file_hash}"
            )

        if _has_semantic_snapshot(extracted_image):
            semantic_dispatches.append(
                {
                    "source_key": image_ref.source_key,
                    "file_hash": image_ref.file_hash,
                    "image_id": extracted_image.id,
                    "semantic_task_id": None,
                    "dispatch_status": "skipped_existing_snapshot",
                    "target_model": target_image_model,
                }
            )
            continue

        submission = create_or_reuse_extracted_image_semantic_task(
            session,
            extracted_image=extracted_image,
            requested_model=requested_image_model,
            target_model=target_image_model,
            use_target_model=True,
            request_id=request_id,
            overwrite_existing_snapshot=False,
        )
        semantic_dispatches.append(
            {
                "source_key": image_ref.source_key,
                "file_hash": image_ref.file_hash,
                "image_id": extracted_image.id,
                "semantic_task_id": str(submission.task.id),
                "dispatch_status": "reused" if submission.reused else "submitted",
                "target_model": target_image_model,
            }
        )

    return semantic_dispatches



def get_active_document_parsing_task_for_document(
    session: Session,
    *,
    document_id: UUID,
    pdf_model_key: str = DEFAULT_DOCUMENT_PARSING_PDF_MODEL,
    image_model_key: str = DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY,
) -> DocumentParsingTask | None:
    """查询同一文档、同一模型组合下的活动任务。"""
    statement = (
        select(DocumentParsingTask)
        .where(
            DocumentParsingTask.document_id == document_id,
            DocumentParsingTask.pdf_model_key == pdf_model_key,
            DocumentParsingTask.image_model_key == image_model_key,
            DocumentParsingTask.status.in_((DocumentParsingTaskStatus.pending, DocumentParsingTaskStatus.running)),
        )
        .order_by(DocumentParsingTask.created_at.desc())
    )
    return session.exec(statement).first()



def get_latest_succeeded_document_parsing_task_for_file_pdf(
    session: Session,
    *,
    file_id: UUID,
    pdf_model_key: str,
) -> DocumentParsingTask | None:
    """查询同一文件、同一 PDF 模型的最近成功任务。"""
    statement = (
        select(DocumentParsingTask)
        .where(
            DocumentParsingTask.file_id == file_id,
            DocumentParsingTask.pdf_model_key == pdf_model_key,
            DocumentParsingTask.status == DocumentParsingTaskStatus.succeeded,
        )
        .order_by(DocumentParsingTask.created_at.desc())
    )
    return session.exec(statement).first()



def get_latest_succeeded_document_parsing_task_for_file_pdf_image(
    session: Session,
    *,
    file_id: UUID,
    pdf_model_key: str,
    image_model_key: str,
) -> DocumentParsingTask | None:
    """查询同一文件、同一模型组合下最近可直接复用的完整成功任务。"""
    statement = (
        select(DocumentParsingTask)
        .where(
            DocumentParsingTask.file_id == file_id,
            DocumentParsingTask.pdf_model_key == pdf_model_key,
            DocumentParsingTask.image_model_key == image_model_key,
            DocumentParsingTask.status == DocumentParsingTaskStatus.succeeded,
        )
        .order_by(DocumentParsingTask.created_at.desc())
    )
    tasks = list(session.exec(statement).all())
    for task in tasks:
        if _task_has_complete_semantic_result(task):
            return task
    return None



def create_or_reuse_document_parsing_task(
    session: Session,
    *,
    document_id: UUID,
    file_id: UUID,
    storage_bucket: str,
    storage_key: str,
    requested_pdf_model: str | None = None,
    requested_image_model: str | None = None,
    force_pdf_parse: bool = False,
    dispatch_semantic_tasks: bool = True,
) -> DocumentParsingTaskSubmissionResult:
    """创建或复用文档解析任务。

    约束：
    - 同一文档在相同 `pdf_model_key + image_model_key` 下只允许存在一条活动任务。

    副作用：
    - 会提交当前数据库事务。

    失败语义：
    - 并发冲突时回滚并复用获胜任务；若未查回获胜任务，则继续抛出原始异常。
    """
    pdf_model_selection = resolve_document_parsing_pdf_model_selection(requested_pdf_model)
    image_model_selection = resolve_document_parsing_image_model_selection(requested_image_model)

    existing = get_active_document_parsing_task_for_document(
        session,
        document_id=document_id,
        pdf_model_key=pdf_model_selection.model_key,
        image_model_key=image_model_selection.model_key,
    )
    if existing is not None:
        return DocumentParsingTaskSubmissionResult(task=existing, reused=True)

    pdf_result_source_task: DocumentParsingTask | None = None
    if not force_pdf_parse:
        if dispatch_semantic_tasks:
            completed_task = get_latest_succeeded_document_parsing_task_for_file_pdf_image(
                session,
                file_id=file_id,
                pdf_model_key=pdf_model_selection.model_key,
                image_model_key=image_model_selection.model_key,
            )
            if completed_task is not None:
                return DocumentParsingTaskSubmissionResult(task=completed_task, reused=True)

        pdf_result_source_task = get_latest_succeeded_document_parsing_task_for_file_pdf(
            session,
            file_id=file_id,
            pdf_model_key=pdf_model_selection.model_key,
        )
        if pdf_result_source_task is not None and not dispatch_semantic_tasks:
            return DocumentParsingTaskSubmissionResult(task=pdf_result_source_task, reused=True)

    task = DocumentParsingTask(
        document_id=document_id,
        file_id=file_id,
        storage_bucket=storage_bucket,
        storage_key=storage_key,
        requested_pdf_model=pdf_model_selection.requested_model,
        target_pdf_model=pdf_model_selection.target_model or DEFAULT_DOCUMENT_PARSING_PDF_MODEL,
        pdf_model_key=pdf_model_selection.model_key,
        requested_image_model=image_model_selection.requested_model,
        target_image_model=image_model_selection.target_model,
        image_model_key=image_model_selection.model_key,
        force_pdf_parse=force_pdf_parse,
        pdf_result_source_task_id=pdf_result_source_task.id if pdf_result_source_task is not None and dispatch_semantic_tasks else None,
        status=DocumentParsingTaskStatus.pending,
    )
    session.add(task)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing_after_conflict = get_active_document_parsing_task_for_document(
            session,
            document_id=document_id,
            pdf_model_key=pdf_model_selection.model_key,
            image_model_key=image_model_selection.model_key,
        )
        if existing_after_conflict is not None:
            return DocumentParsingTaskSubmissionResult(task=existing_after_conflict, reused=True)
        raise

    session.refresh(task)
    return DocumentParsingTaskSubmissionResult(task=task, reused=False)



def get_document_parsing_task_by_id(session: Session, *, task_id: UUID) -> DocumentParsingTask | None:
    """按任务 id 查询文档解析任务。"""
    statement = select(DocumentParsingTask).where(DocumentParsingTask.id == task_id)
    return session.exec(statement).first()



def get_latest_document_parsing_task_for_document_file(
    session: Session,
    *,
    document_id: UUID,
    file_id: UUID,
) -> DocumentParsingTask | None:
    """查询当前文档文件最近一次文档解析任务记录。"""
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
    """将文档解析任务标记为失败并提交。"""
    now = utc_now()
    task.status = DocumentParsingTaskStatus.failed
    task.error_message = error_message
    task.finished_at = now
    task.updated_at = now
    session.add(task)
    session.commit()



def recover_orphaned_document_parsing_tasks() -> int:
    """恢复进程重启后遗留在 running 状态的文档解析孤儿任务。

    说明：
    - 原执行上下文已丢失，继续保留 running 只会阻塞后续复用与重试，因此统一改为 failed。
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
    """领取一条待执行文档解析任务并切换到 running。

    说明：
    - 使用 `FOR UPDATE SKIP LOCKED`，确保多个 worker 并发时不会重复消费同一任务。
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
    """执行单条文档解析任务。

    流程：
    1. 优先尝试复用已有成功任务的 PDF 结果。
    2. 若不可复用，则调用 file-convert-service 完成 PDF 转 markdown 与图片抽取。
    3. 仅在实际执行 PDF 解析时落库抽取图片元数据。
    4. 为仍缺少语义快照的图片派发语义任务。
    5. 记录派发结果并结束顶层任务。

    副作用：
    - 可能调用 file-convert-service。
    - 会写入 `document_parsing_tasks` 与 `extracted_images`，并可能创建抽取图片语义任务。

    失败语义：
    - 任一步骤失败都会把顶层任务标记为 failed。
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

        markdown: str | None = None
        image_hashes: dict[str, str] = {}
        image_refs: list[DocumentParsingImageRef] = []

        if task.pdf_result_source_task_id is not None:
            source_task = session.get(DocumentParsingTask, task.pdf_result_source_task_id)
            if _is_reusable_pdf_result_source(source_task):
                markdown = source_task.markdown
                image_hashes = dict(source_task.image_hashes or {})
                image_refs = _build_image_refs_from_image_hashes(image_hashes)
            else:
                logger.info(
                    "Falling back to live PDF parsing because cached source task is unavailable, task_id=%s, source_task_id=%s",
                    task_id,
                    task.pdf_result_source_task_id,
                )
                task.pdf_result_source_task_id = None

        if markdown is None:
            parsing_result, error = file_convert_client.convert_pdf_to_markdown(
                storage_key=task.storage_key,
                task_id=str(task.id),
                model=task.target_pdf_model,
            )
            if error is not None or parsing_result is None:
                logger.warning("document parsing task failed on file-convert-service, task_id=%s, error=%s", task_id, error)
                _mark_task_failed(
                    session,
                    task=task,
                    error_message=error or "file-convert-service parsing failed",
                )
                return

            markdown = parsing_result.markdown
            image_hashes = dict(parsing_result.image_hashes or {})
            image_refs = _build_image_refs_from_uploaded_images(parsing_result.uploaded_images)

            try:
                persist_extracted_images(session, uploaded_images=parsing_result.uploaded_images)
            except (ExtractedImagePersistenceError, SQLAlchemyError):
                logger.exception("Failed to persist extracted images for document parsing task, task_id=%s", task_id)
                _mark_task_failed(
                    session,
                    task=task,
                    error_message="Failed to persist extracted images",
                )
                return

        try:
            semantic_dispatches = _dispatch_semantic_tasks_for_image_refs(
                session,
                image_refs=image_refs,
                request_id=str(task.id),
                requested_image_model=task.requested_image_model,
                target_image_model=task.target_image_model,
            )
        except (DocumentParsingSemanticDispatchError, SQLAlchemyError):
            logger.exception("Failed to dispatch extracted image semantic tasks for document parsing task, task_id=%s", task_id)
            _mark_task_failed(
                session,
                task=task,
                error_message="Failed to dispatch extracted image semantic tasks",
            )
            return

        now = utc_now()
        task.status = DocumentParsingTaskStatus.succeeded
        task.markdown = markdown
        task.image_hashes = image_hashes
        task.semantic_dispatches = semantic_dispatches
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
    """处理一条待执行文档解析任务。"""
    task_id = claim_next_pending_document_parsing_task_id()
    if task_id is None:
        return False

    execute_document_parsing_task(task_id, client=client)
    return True


class DocumentParsingTaskWorker:
    """文档解析任务轮询 worker。"""

    def __init__(self, *, poll_interval_seconds: float = 1.0) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._runner_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """启动 worker，并在正式轮询前恢复孤儿任务。"""
        if self._runner_task is not None and not self._runner_task.done():
            return

        self._stop_event = asyncio.Event()
        recovered = await asyncio.to_thread(recover_orphaned_document_parsing_tasks)
        if recovered > 0:
            logger.warning("Recovered orphaned running document parsing tasks, count=%s", recovered)

        self._runner_task = asyncio.create_task(self._run_loop(), name="document-parsing-task-worker")
        logger.info("document parsing task worker started")

    async def stop(self) -> None:
        """停止 worker，并等待当前轮询循环退出。"""
        runner_task = self._runner_task
        if runner_task is None:
            return

        self._stop_event.set()
        await runner_task
        self._runner_task = None
        logger.info("document parsing task worker stopped")

    async def _run_loop(self) -> None:
        """持续轮询待执行任务，直到收到停止信号。"""
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
    """按环境变量构造文档解析任务 worker 单例。"""
    poll_interval_seconds = float(os.getenv("DOCUMENT_PARSING_TASK_WORKER_POLL_INTERVAL_SECONDS", "1.0"))
    return DocumentParsingTaskWorker(poll_interval_seconds=poll_interval_seconds)



def is_document_parsing_task_worker_enabled() -> bool:
    """读取文档解析任务 worker 开关。"""
    value = os.getenv("DOCUMENT_PARSING_TASK_WORKER_ENABLED")
    return _to_bool(value, default=True)

