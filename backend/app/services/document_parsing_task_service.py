"""文档解析聚合任务服务。

职责：
1. 解析文档解析链路中的版面模型与图片语义模型选择规则。
2. 负责任务创建/复用、图片项分发与聚合状态重算。
3. 协调 `LayoutAnalysisTask`、`ExtractedImageSemanticTask` 与聚合父任务之间的状态同步。

说明：
- `DocumentParsingTask` 是严格聚合父任务，状态语义不等同于版面分析阶段本身。
- 本模块只负责数据库编排与状态流转，不直接执行 PDF 解析或 LLM 识别。
- 图片语义结果优先读取模型作用域快照，其次回退到成功的语义任务结果。
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from ..database import engine
from ..models import (
    DEFAULT_DOCUMENT_PARSING_IMAGE_MODEL_KEY,
    DocumentParsingImageItem,
    DocumentParsingImageItemResultSource,
    DocumentParsingImageItemStatus,
    DocumentParsingTask,
    DocumentParsingTaskStatus,
    ExtractedImage,
    ExtractedImageSemanticSnapshot,
    ExtractedImageSemanticTask,
    ExtractedImageSemanticTaskStatus,
    LayoutAnalysisTask,
    LayoutAnalysisTaskStatus,
)
from ..models.common import utc_now
from .extracted_image_semantic_service import (
    get_extracted_image_semantic_target_model_key,
    resolve_extracted_image_semantic_model,
)
from .extracted_image_semantic_task_service import create_or_reuse_extracted_image_semantic_task
from .layout_analysis_task_service import create_or_reuse_layout_analysis_task, resolve_layout_analysis_model_selection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentParsingTaskSubmissionResult:
    """文档解析任务提交结果。"""

    task: DocumentParsingTask
    reused: bool


@dataclass(frozen=True)
class DocumentParsingModelSelection:
    """文档解析子阶段模型选择结果。"""

    requested_model: str | None
    target_model: str | None
    model_key: str


@dataclass(frozen=True)
class DocumentParsingImageRef:
    """版面分析产出的单张图片引用。"""

    source_key: str
    file_hash: str


@dataclass(frozen=True)
class DocumentParsingImageSemanticResult:
    """聚合后的图片语义结果快照。"""

    description: str
    result_model: str | None
    source_task_id: UUID | None
    updated_at: datetime



def _normalize_optional_model(value: str | None) -> str | None:
    """标准化可选模型名，去掉空白并把空字符串转为 `None`。"""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None



def resolve_document_parsing_image_model_selection(requested_model: str | None) -> DocumentParsingModelSelection:
    """解析文档解析链路中的图片语义模型选择。"""
    normalized_requested_model = _normalize_optional_model(requested_model)
    target_model = resolve_extracted_image_semantic_model(normalized_requested_model)
    model_key = get_extracted_image_semantic_target_model_key(target_model)
    return DocumentParsingModelSelection(
        requested_model=normalized_requested_model,
        target_model=target_model,
        model_key=model_key,
    )



def _build_image_refs_from_image_hashes(image_hashes: Mapping[str, str] | None) -> list[DocumentParsingImageRef]:
    """将 `image_hashes` 映射转换为稳定的图片引用列表。"""
    if not image_hashes:
        return []
    return [
        DocumentParsingImageRef(source_key=source_key, file_hash=file_hash)
        for source_key, file_hash in image_hashes.items()
        if source_key and file_hash
    ]



def _load_extracted_images_by_hash(session: Session, *, file_hashes: list[str]) -> dict[str, ExtractedImage]:
    """按文件哈希批量加载已落库的抽取图片。"""
    normalized_hashes = [file_hash for file_hash in file_hashes if file_hash]
    if not normalized_hashes:
        return {}
    statement = select(ExtractedImage).where(ExtractedImage.file_hash.in_(normalized_hashes))
    return {image.file_hash: image for image in session.exec(statement).all()}



def get_active_document_parsing_task_for_document(
    session: Session,
    *,
    document_id: UUID,
    layout_model_key: str,
    image_model_key: str,
) -> DocumentParsingTask | None:
    """获取文档当前仍在进行中的文档解析聚合任务。"""
    statement = (
        select(DocumentParsingTask)
        .where(
            DocumentParsingTask.document_id == document_id,
            DocumentParsingTask.layout_model_key == layout_model_key,
            DocumentParsingTask.image_model_key == image_model_key,
            DocumentParsingTask.status.in_((DocumentParsingTaskStatus.pending, DocumentParsingTaskStatus.running)),
        )
        .order_by(DocumentParsingTask.created_at.desc())
    )
    return session.exec(statement).first()



def get_latest_succeeded_document_parsing_task_for_file(
    session: Session,
    *,
    file_id: UUID,
    layout_model_key: str,
    image_model_key: str,
) -> DocumentParsingTask | None:
    """获取同文件最近一次完整成功的文档解析结果。"""
    statement = (
        select(DocumentParsingTask)
        .where(
            DocumentParsingTask.file_id == file_id,
            DocumentParsingTask.layout_model_key == layout_model_key,
            DocumentParsingTask.image_model_key == image_model_key,
            DocumentParsingTask.status == DocumentParsingTaskStatus.succeeded,
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
    requested_layout_model: str | None = None,
    requested_image_model: str | None = None,
    force_layout_analysis: bool = False,
) -> DocumentParsingTaskSubmissionResult:
    """创建或复用文档解析聚合任务。

    复用语义：
    - 优先复用同文档、同模型组合下仍处于 `pending/running` 的活跃任务。
    - 未强制重跑版面分析时，可复用同文件最近一次完整成功结果。
    """
    layout_selection = resolve_layout_analysis_model_selection(requested_layout_model)
    image_selection = resolve_document_parsing_image_model_selection(requested_image_model)

    existing = get_active_document_parsing_task_for_document(
        session,
        document_id=document_id,
        layout_model_key=layout_selection.model_key,
        image_model_key=image_selection.model_key,
    )
    if existing is not None:
        return DocumentParsingTaskSubmissionResult(task=existing, reused=True)

    if not force_layout_analysis:
        completed_task = get_latest_succeeded_document_parsing_task_for_file(
            session,
            file_id=file_id,
            layout_model_key=layout_selection.model_key,
            image_model_key=image_selection.model_key,
        )
        if completed_task is not None:
            return DocumentParsingTaskSubmissionResult(task=completed_task, reused=True)

    layout_submission = create_or_reuse_layout_analysis_task(
        session,
        document_id=document_id,
        file_id=file_id,
        storage_bucket=storage_bucket,
        storage_key=storage_key,
        requested_layout_model=requested_layout_model,
        force_layout_analysis=force_layout_analysis,
    )
    task = DocumentParsingTask(
        document_id=document_id,
        file_id=file_id,
        storage_bucket=storage_bucket,
        storage_key=storage_key,
        requested_layout_model=layout_selection.requested_model,
        target_layout_model=layout_selection.target_model,
        layout_model_key=layout_selection.model_key,
        requested_image_model=image_selection.requested_model,
        target_image_model=image_selection.target_model,
        image_model_key=image_selection.model_key,
        force_layout_analysis=force_layout_analysis,
        layout_task_id=layout_submission.task.id,
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
            layout_model_key=layout_selection.model_key,
            image_model_key=image_selection.model_key,
        )
        if existing_after_conflict is not None:
            return DocumentParsingTaskSubmissionResult(task=existing_after_conflict, reused=True)
        raise

    session.refresh(task)
    synchronize_document_parsing_task(task.id)
    session.refresh(task)
    return DocumentParsingTaskSubmissionResult(task=task, reused=False)



def get_document_parsing_task_by_id(session: Session, *, task_id: UUID) -> DocumentParsingTask | None:
    """按任务 ID 查询文档解析聚合任务。"""
    statement = select(DocumentParsingTask).where(DocumentParsingTask.id == task_id)
    return session.exec(statement).first()



def get_latest_document_parsing_task_for_document_file(
    session: Session,
    *,
    document_id: UUID,
    file_id: UUID,
) -> DocumentParsingTask | None:
    """获取文档当前文件最近一次文档解析任务。"""
    statement = (
        select(DocumentParsingTask)
        .where(DocumentParsingTask.document_id == document_id, DocumentParsingTask.file_id == file_id)
        .order_by(DocumentParsingTask.created_at.desc())
    )
    return session.exec(statement).first()



def get_latest_succeeded_document_parsing_task_for_document_file(
    session: Session,
    *,
    document_id: UUID,
    file_id: UUID,
) -> DocumentParsingTask | None:
    """获取文档当前文件最近一次成功的文档解析任务。"""
    statement = (
        select(DocumentParsingTask)
        .where(
            DocumentParsingTask.document_id == document_id,
            DocumentParsingTask.file_id == file_id,
            DocumentParsingTask.status == DocumentParsingTaskStatus.succeeded,
        )
        .order_by(DocumentParsingTask.created_at.desc())
    )
    return session.exec(statement).first()


def get_default_document_parsing_task_for_document_file(
    session: Session,
    *,
    document_id: UUID,
    file_id: UUID,
) -> DocumentParsingTask | None:
    """获取对外默认展示的文档解析任务。

    说明：
    - 若最新任务未失败，则直接返回最新任务。
    - 若最新任务失败，则优先回退到最近一次成功任务。
    """
    latest_task = get_latest_document_parsing_task_for_document_file(
        session,
        document_id=document_id,
        file_id=file_id,
    )
    if latest_task is None:
        return None
    if latest_task.status != DocumentParsingTaskStatus.failed:
        return latest_task

    latest_succeeded_task = get_latest_succeeded_document_parsing_task_for_document_file(
        session,
        document_id=document_id,
        file_id=file_id,
    )
    if latest_succeeded_task is not None:
        return latest_succeeded_task
    return latest_task


def get_document_parsing_image_items(session: Session, *, task_id: UUID) -> list[DocumentParsingImageItem]:
    """获取聚合任务下的全部图片项，按创建顺序返回。"""
    statement = (
        select(DocumentParsingImageItem)
        .where(DocumentParsingImageItem.document_parsing_task_id == task_id)
        .order_by(DocumentParsingImageItem.id.asc())
    )
    return list(session.exec(statement).all())



def get_layout_task_for_document_parsing_task(session: Session, *, task: DocumentParsingTask) -> LayoutAnalysisTask | None:
    """加载聚合任务绑定的版面分析任务。"""
    return session.get(LayoutAnalysisTask, task.layout_task_id)



def get_document_parsing_image_semantic_result(
    session: Session,
    *,
    item: DocumentParsingImageItem,
    image_model_key: str,
) -> DocumentParsingImageSemanticResult | None:
    """获取图片项当前可见的语义结果。

    读取优先级：
    - 先读模型作用域快照。
    - 若无快照，则回退到绑定且已成功的语义任务。
    """
    snapshot = _get_semantic_snapshot(
        session,
        extracted_image_id=item.extracted_image_id,
        target_model_key=image_model_key,
    )
    if snapshot is not None:
        return DocumentParsingImageSemanticResult(
            description=snapshot.description,
            result_model=snapshot.result_model,
            source_task_id=snapshot.source_task_id,
            updated_at=snapshot.updated_at,
        )

    if item.semantic_task_id is None:
        return None

    semantic_task = session.get(ExtractedImageSemanticTask, item.semantic_task_id)
    if semantic_task is None or semantic_task.status != ExtractedImageSemanticTaskStatus.succeeded:
        return None
    if semantic_task.description is None:
        return None

    return DocumentParsingImageSemanticResult(
        description=semantic_task.description,
        result_model=semantic_task.result_model,
        source_task_id=semantic_task.id,
        updated_at=semantic_task.updated_at,
    )



def _get_semantic_snapshot(
    session: Session,
    *,
    extracted_image_id: int,
    target_model_key: str,
) -> ExtractedImageSemanticSnapshot | None:
    """获取图片在指定目标模型下最近的语义快照。"""
    statement = (
        select(ExtractedImageSemanticSnapshot)
        .where(
            ExtractedImageSemanticSnapshot.extracted_image_id == extracted_image_id,
            ExtractedImageSemanticSnapshot.target_model_key == target_model_key,
        )
        .order_by(ExtractedImageSemanticSnapshot.updated_at.desc())
    )
    return session.exec(statement).first()



def _refresh_image_item_statuses_from_semantic_tasks(session: Session, *, task_id: UUID) -> None:
    """根据绑定的语义任务状态回刷图片项状态。"""
    for item in get_document_parsing_image_items(session, task_id=task_id):
        if item.semantic_task_id is None:
            continue
        semantic_task = session.get(ExtractedImageSemanticTask, item.semantic_task_id)
        if semantic_task is None:
            continue

        if semantic_task.status == ExtractedImageSemanticTaskStatus.succeeded:
            item.status = DocumentParsingImageItemStatus.succeeded
            item.error_message = None
        elif semantic_task.status == ExtractedImageSemanticTaskStatus.failed:
            item.status = DocumentParsingImageItemStatus.failed
            item.error_message = semantic_task.error_message
        elif semantic_task.status == ExtractedImageSemanticTaskStatus.running:
            item.status = DocumentParsingImageItemStatus.running
            item.error_message = None
        else:
            item.status = DocumentParsingImageItemStatus.pending
            item.error_message = None
        item.updated_at = utc_now()
        session.add(item)



def _mark_task_failed(task: DocumentParsingTask, *, error_message: str) -> None:
    """将聚合任务标记为失败并补齐失败时间戳。"""
    now = utc_now()
    task.status = DocumentParsingTaskStatus.failed
    task.error_message = error_message
    task.finished_at = now
    task.updated_at = now
    if task.started_at is None:
        task.started_at = now



def _recompute_document_parsing_task_state(
    session: Session,
    *,
    task: DocumentParsingTask,
    layout_task: LayoutAnalysisTask | None,
) -> None:
    """依据版面任务和图片项状态重算聚合任务状态。

    聚合语义：
    - layout 不存在或失败时，父任务失败。
    - layout 成功后，全部必要图片成功才算最终成功。
    - 只要仍有必要图片未完成，则父任务保持 running。
    """
    now = utc_now()
    items = get_document_parsing_image_items(session, task_id=task.id)
    task.image_total_count = len(items)
    task.image_succeeded_count = sum(1 for item in items if item.status == DocumentParsingImageItemStatus.succeeded)
    task.image_failed_count = sum(1 for item in items if item.status == DocumentParsingImageItemStatus.failed)

    if layout_task is None:
        _mark_task_failed(task, error_message="Layout analysis task not found")
    elif layout_task.status == LayoutAnalysisTaskStatus.failed:
        _mark_task_failed(task, error_message=layout_task.error_message or "Layout analysis failed")
    elif layout_task.status == LayoutAnalysisTaskStatus.succeeded:
        task.markdown = layout_task.markdown
        task.image_hashes = dict(layout_task.image_hashes or {})
        if task.started_at is None:
            task.started_at = now
        if task.image_failed_count > 0:
            _mark_task_failed(task, error_message="Image semantic analysis failed")
        elif task.image_total_count == 0 or task.image_succeeded_count == task.image_total_count:
            task.status = DocumentParsingTaskStatus.succeeded
            task.error_message = None
            task.finished_at = now
            task.updated_at = now
        else:
            task.status = DocumentParsingTaskStatus.running
            task.error_message = None
            task.finished_at = None
            task.updated_at = now
    elif layout_task.status == LayoutAnalysisTaskStatus.running:
        task.status = DocumentParsingTaskStatus.running
        task.error_message = None
        task.finished_at = None
        task.updated_at = now
        if task.started_at is None:
            task.started_at = layout_task.started_at or now
    else:
        task.status = DocumentParsingTaskStatus.pending
        task.error_message = None
        task.finished_at = None
        task.updated_at = now

    session.add(task)



def _dispatch_image_items_if_needed(session: Session, *, task: DocumentParsingTask, layout_task: LayoutAnalysisTask) -> None:
    """按版面分析产出的图片列表初始化缺失的图片项。

    说明：
    - 已存在的图片项不会重复创建。
    - 如命中语义快照则直接生成 succeeded 图片项，否则提交/复用语义任务。
    """
    existing_items = get_document_parsing_image_items(session, task_id=task.id)
    existing_items_by_source_key = {item.source_key: item for item in existing_items}

    image_refs = _build_image_refs_from_image_hashes(layout_task.image_hashes)
    if not image_refs:
        return

    images_by_hash = _load_extracted_images_by_hash(session, file_hashes=[image_ref.file_hash for image_ref in image_refs])
    for image_ref in image_refs:
        if image_ref.source_key in existing_items_by_source_key:
            continue

        extracted_image = images_by_hash.get(image_ref.file_hash)
        if extracted_image is None or extracted_image.id is None:
            raise RuntimeError(f"Extracted image not found for hash={image_ref.file_hash}")

        snapshot = _get_semantic_snapshot(
            session,
            extracted_image_id=extracted_image.id,
            target_model_key=task.image_model_key,
        )
        if snapshot is not None:
            item = DocumentParsingImageItem(
                document_parsing_task_id=task.id,
                source_key=image_ref.source_key,
                file_hash=image_ref.file_hash,
                extracted_image_id=extracted_image.id,
                semantic_task_id=None,
                status=DocumentParsingImageItemStatus.succeeded,
                result_source=DocumentParsingImageItemResultSource.semantic_snapshot,
                error_message=None,
            )
            session.add(item)
            continue

        submission = create_or_reuse_extracted_image_semantic_task(
            session,
            extracted_image=extracted_image,
            requested_model=task.requested_image_model,
            target_model=task.target_image_model,
            use_target_model=True,
            request_id=str(task.id),
            overwrite_existing_snapshot=False,
        )
        item = DocumentParsingImageItem(
            document_parsing_task_id=task.id,
            source_key=image_ref.source_key,
            file_hash=image_ref.file_hash,
            extracted_image_id=extracted_image.id,
            semantic_task_id=submission.task.id,
            status=(
                DocumentParsingImageItemStatus.running
                if submission.task.status == ExtractedImageSemanticTaskStatus.running
                else DocumentParsingImageItemStatus.pending
            ),
            result_source=(
                DocumentParsingImageItemResultSource.reused_semantic_task
                if submission.reused
                else DocumentParsingImageItemResultSource.submitted_semantic_task
            ),
            error_message=None,
        )
        session.add(item)



def synchronize_document_parsing_task(task_id: UUID) -> None:
    """同步单个文档解析聚合任务的全部子状态。

    同步步骤：
    - 若版面分析已成功，先补齐需要的图片项。
    - 然后回刷图片项状态。
    - 最后依据最新子状态重算聚合任务状态。
    """
    with Session(engine) as session:
        task = session.get(DocumentParsingTask, task_id)
        if task is None:
            return

        layout_task = session.get(LayoutAnalysisTask, task.layout_task_id)
        if layout_task is not None and layout_task.status == LayoutAnalysisTaskStatus.succeeded:
            try:
                _dispatch_image_items_if_needed(session, task=task, layout_task=layout_task)
            except (RuntimeError, SQLAlchemyError):
                logger.exception("Failed to initialize document parsing image items, task_id=%s", task_id)
                _mark_task_failed(task, error_message="Failed to initialize image analysis items")
                session.add(task)
                session.commit()
                return

        _refresh_image_item_statuses_from_semantic_tasks(session, task_id=task_id)
        _recompute_document_parsing_task_state(session, task=task, layout_task=layout_task)
        session.commit()



def process_document_parsing_tasks_for_layout_task(layout_task_id: UUID) -> None:
    """同步某个版面分析任务绑定的全部聚合任务。"""
    with Session(engine) as session:
        statement = select(DocumentParsingTask.id).where(DocumentParsingTask.layout_task_id == layout_task_id)
        task_ids = list(session.exec(statement).all())
    for task_id in task_ids:
        synchronize_document_parsing_task(task_id)



def process_document_parsing_tasks_for_semantic_task(semantic_task_id: UUID) -> None:
    """同步某个图片语义任务影响到的全部聚合任务。"""
    with Session(engine) as session:
        statement = select(DocumentParsingImageItem.document_parsing_task_id).where(
            DocumentParsingImageItem.semantic_task_id == semantic_task_id
        )
        task_ids = sorted(set(session.exec(statement).all()))
    for task_id in task_ids:
        synchronize_document_parsing_task(task_id)

