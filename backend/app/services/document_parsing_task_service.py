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
    task: DocumentParsingTask
    reused: bool


@dataclass(frozen=True)
class DocumentParsingModelSelection:
    requested_model: str | None
    target_model: str | None
    model_key: str


@dataclass(frozen=True)
class DocumentParsingImageRef:
    source_key: str
    file_hash: str


@dataclass(frozen=True)
class DocumentParsingImageSemanticResult:
    description: str
    result_model: str | None
    source_task_id: UUID | None
    updated_at: datetime



def _normalize_optional_model(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None



def resolve_document_parsing_image_model_selection(requested_model: str | None) -> DocumentParsingModelSelection:
    normalized_requested_model = _normalize_optional_model(requested_model)
    target_model = resolve_extracted_image_semantic_model(normalized_requested_model)
    model_key = get_extracted_image_semantic_target_model_key(target_model)
    return DocumentParsingModelSelection(
        requested_model=normalized_requested_model,
        target_model=target_model,
        model_key=model_key,
    )



def _build_image_refs_from_image_hashes(image_hashes: Mapping[str, str] | None) -> list[DocumentParsingImageRef]:
    if not image_hashes:
        return []
    return [
        DocumentParsingImageRef(source_key=source_key, file_hash=file_hash)
        for source_key, file_hash in image_hashes.items()
        if source_key and file_hash
    ]



def _load_extracted_images_by_hash(session: Session, *, file_hashes: list[str]) -> dict[str, ExtractedImage]:
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



def get_document_parsing_image_items(session: Session, *, task_id: UUID) -> list[DocumentParsingImageItem]:
    statement = (
        select(DocumentParsingImageItem)
        .where(DocumentParsingImageItem.document_parsing_task_id == task_id)
        .order_by(DocumentParsingImageItem.id.asc())
    )
    return list(session.exec(statement).all())



def get_layout_task_for_document_parsing_task(session: Session, *, task: DocumentParsingTask) -> LayoutAnalysisTask | None:
    return session.get(LayoutAnalysisTask, task.layout_task_id)



def get_document_parsing_image_semantic_result(
    session: Session,
    *,
    item: DocumentParsingImageItem,
    image_model_key: str,
) -> DocumentParsingImageSemanticResult | None:
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
    with Session(engine) as session:
        statement = select(DocumentParsingTask.id).where(DocumentParsingTask.layout_task_id == layout_task_id)
        task_ids = list(session.exec(statement).all())
    for task_id in task_ids:
        synchronize_document_parsing_task(task_id)



def process_document_parsing_tasks_for_semantic_task(semantic_task_id: UUID) -> None:
    with Session(engine) as session:
        statement = select(DocumentParsingImageItem.document_parsing_task_id).where(
            DocumentParsingImageItem.semantic_task_id == semantic_task_id
        )
        task_ids = sorted(set(session.exec(statement).all()))
    for task_id in task_ids:
        synchronize_document_parsing_task(task_id)

