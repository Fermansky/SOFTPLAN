"""提取图片资源路由。

职责：
1. 提供 ExtractedImage 的基础 CRUD 接口。
2. 暴露图片语义识别任务的提交、按任务查询、按图片查询最新结果接口。
3. 负责把任务服务结果映射为稳定的 API 响应模型。
"""

import logging
from datetime import datetime
from enum import Enum
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ...core.logging import build_log_extra, get_request_id
from ...database import get_session
from ...models import (
    ExtractedImage,
    ExtractedImageCreate,
    ExtractedImageRead,
    ExtractedImageSemanticTask,
    ExtractedImageSemanticTaskStatus,
    ExtractedImageUpdate,
)
from ...services import (
    create_or_reuse_extracted_image_semantic_task,
    get_extracted_image_semantic_task_by_id,
    get_latest_extracted_image_semantic_task_for_image,
)
from ..dependencies import get_extracted_image_or_404

router = APIRouter(prefix="/extracted-images", tags=["extracted-images"])
logger = logging.getLogger(__name__)


class ExtractedImageSemanticTaskCreateRequest(BaseModel):
    request_id: str | None = None
    model: str | None = None


class ExtractedImageSemanticTaskRead(BaseModel):
    id: UUID
    image_id: int
    status: ExtractedImageSemanticTaskStatus
    requested_model: str | None = None
    target_model: str | None = None
    result_model: str | None = None
    request_id: str | None = None
    attempt_count: int
    reused: bool = False
    description: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class ExtractedImageSemanticResultStatus(str, Enum):
    no_task = "no_task"
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class ExtractedImageSemanticImageResultRead(BaseModel):
    image_id: int
    status: ExtractedImageSemanticResultStatus
    task_id: UUID | None = None
    requested_model: str | None = None
    target_model: str | None = None
    result_model: str | None = None
    description: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime | None = None


@router.post("", response_model=ExtractedImageRead, status_code=status.HTTP_201_CREATED)
def create_extracted_image(
    payload: ExtractedImageCreate,
    session: Session = Depends(get_session),
) -> ExtractedImage:
    extracted_image = ExtractedImage(**payload.model_dump())
    session.add(extracted_image)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Extracted image hash already exists",
        ) from exc
    session.refresh(extracted_image)
    return extracted_image


@router.get("", response_model=list[ExtractedImageRead])
def list_extracted_images(
    session: Session = Depends(get_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ExtractedImage]:
    statement = select(ExtractedImage).order_by(ExtractedImage.created_at.desc()).offset(offset).limit(limit)
    return list(session.exec(statement).all())


@router.get("/semantic-description/tasks/{task_id}", response_model=ExtractedImageSemanticTaskRead)
def get_extracted_image_semantic_task(task_id: UUID, session: Session = Depends(get_session)) -> ExtractedImageSemanticTaskRead:
    task = get_extracted_image_semantic_task_by_id(session, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="extracted image semantic task not found")
    return _to_task_read(task)


@router.get("/{image_id}", response_model=ExtractedImageRead)
def get_extracted_image(image_id: int, session: Session = Depends(get_session)) -> ExtractedImage:
    return get_extracted_image_or_404(image_id, session)


@router.post(
    "/{image_id}/semantic-description",
    response_model=ExtractedImageSemanticTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_extracted_image_semantic_task(
    image_id: int,
    payload: ExtractedImageSemanticTaskCreateRequest | None = Body(default=None),
    session: Session = Depends(get_session),
) -> ExtractedImageSemanticTaskRead:
    extracted_image = get_extracted_image_or_404(image_id, session)
    request_id = (payload.request_id if payload is not None else None) or get_request_id()
    requested_model = payload.model if payload is not None else None

    logger.info(
        "Received extracted image semantic task submission",
        extra=build_log_extra(
            "extracted_image.semantic_task_create.started",
            image_id=image_id,
            request_id=request_id,
            requested_model=requested_model or "<default>",
        ),
    )
    try:
        submission = create_or_reuse_extracted_image_semantic_task(
            session,
            extracted_image=extracted_image,
            requested_model=requested_model,
            request_id=request_id,
        )
    except IntegrityError as exc:
        session.rollback()
        logger.exception(
            "Failed to create extracted image semantic task",
            extra=build_log_extra(
                "extracted_image.semantic_task_create.failed",
                image_id=image_id,
                request_id=request_id,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="extracted image semantic task conflict",
        ) from exc

    logger.info(
        "Extracted image semantic task submitted",
        extra=build_log_extra(
            "extracted_image.semantic_task_create.succeeded",
            image_id=image_id,
            request_id=request_id,
            task_id=str(submission.task.id),
            reused=submission.reused,
        ),
    )
    return _to_task_read(submission.task, reused=submission.reused)


@router.get("/{image_id}/semantic-description", response_model=ExtractedImageSemanticImageResultRead)
def get_extracted_image_semantic_result(
    image_id: int,
    session: Session = Depends(get_session),
) -> ExtractedImageSemanticImageResultRead:
    extracted_image = get_extracted_image_or_404(image_id, session)
    task = get_latest_extracted_image_semantic_task_for_image(session, extracted_image_id=extracted_image.id)
    if task is None:
        return ExtractedImageSemanticImageResultRead(
            image_id=extracted_image.id,
            status=ExtractedImageSemanticResultStatus.no_task,
        )

    return _to_image_result_read(task)


@router.patch("/{image_id}", response_model=ExtractedImageRead)
def update_extracted_image(
    image_id: int,
    payload: ExtractedImageUpdate,
    session: Session = Depends(get_session),
) -> ExtractedImage:
    extracted_image = get_extracted_image_or_404(image_id, session)
    update_data = payload.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(extracted_image, field_name, value)

    session.add(extracted_image)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Extracted image hash already exists",
        ) from exc
    session.refresh(extracted_image)
    return extracted_image


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_extracted_image(image_id: int, session: Session = Depends(get_session)) -> Response:
    extracted_image = get_extracted_image_or_404(image_id, session)
    session.delete(extracted_image)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _to_task_read(task: ExtractedImageSemanticTask, *, reused: bool = False) -> ExtractedImageSemanticTaskRead:
    return ExtractedImageSemanticTaskRead(
        id=task.id,
        image_id=task.extracted_image_id,
        status=task.status,
        requested_model=task.requested_model,
        target_model=task.target_model,
        result_model=task.result_model,
        request_id=task.request_id,
        attempt_count=task.attempt_count,
        reused=reused,
        description=task.description,
        error_message=task.error_message,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        updated_at=task.updated_at,
    )


def _to_image_result_read(task: ExtractedImageSemanticTask) -> ExtractedImageSemanticImageResultRead:
    return ExtractedImageSemanticImageResultRead(
        image_id=task.extracted_image_id,
        status=ExtractedImageSemanticResultStatus(task.status.value),
        task_id=task.id,
        requested_model=task.requested_model,
        target_model=task.target_model,
        result_model=task.result_model if task.status == ExtractedImageSemanticTaskStatus.succeeded else None,
        description=task.description if task.status == ExtractedImageSemanticTaskStatus.succeeded else None,
        error_message=task.error_message if task.status == ExtractedImageSemanticTaskStatus.failed else None,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        updated_at=task.updated_at,
    )
