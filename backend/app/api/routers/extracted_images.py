"""抽取图片路由。

职责：
1. 提供抽取图片的创建、列表、详情、更新与删除接口。
2. 提供图片语义任务的创建、查询与结果读取接口。

说明：
- 语义任务创建遵循服务层的去重复用规则。
- 删除图片会物理删除对应数据库记录。
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
    """创建抽取图片语义任务的请求体。"""

    request_id: str | None = None
    model: str | None = None
    overwrite_existing_snapshot: bool = False


class ExtractedImageSemanticTaskRead(BaseModel):
    """抽取图片语义任务读取视图。"""

    id: UUID
    image_id: int
    status: ExtractedImageSemanticTaskStatus
    requested_model: str | None = None
    target_model: str | None = None
    overwrite_existing_snapshot: bool = False
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
    """按图片读取语义结果时的聚合状态。"""

    no_task = "no_task"
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class ExtractedImageSemanticImageResultRead(BaseModel):
    """按图片读取语义结果的响应视图。"""

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
    """创建抽取图片记录。

    失败语义：
    - 当图片哈希冲突时返回 409。
    """

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
    """分页返回抽取图片列表。"""

    statement = select(ExtractedImage).order_by(ExtractedImage.created_at.desc()).offset(offset).limit(limit)
    return list(session.exec(statement).all())


@router.get("/semantic-description/tasks/{task_id}", response_model=ExtractedImageSemanticTaskRead)
def get_extracted_image_semantic_task(task_id: UUID, session: Session = Depends(get_session)) -> ExtractedImageSemanticTaskRead:
    """返回单个抽取图片语义任务详情，未命中时返回 404。"""

    task = get_extracted_image_semantic_task_by_id(session, task_id=task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="extracted image semantic task not found")
    return _to_task_read(task)


@router.get("/{image_id}", response_model=ExtractedImageRead)
def get_extracted_image(image_id: int, session: Session = Depends(get_session)) -> ExtractedImage:
    """返回单个抽取图片详情，未命中时返回 404。"""

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
    """为抽取图片创建或复用语义任务。

    副作用：
    - 调用服务层提交或复用语义任务。
    - 失败时可能回滚事务。

    失败语义：
    - 并发冲突返回 409。
    """

    extracted_image = get_extracted_image_or_404(image_id, session)
    request_id = (payload.request_id if payload is not None else None) or get_request_id()
    requested_model = payload.model if payload is not None else None
    overwrite_existing_snapshot = payload.overwrite_existing_snapshot if payload is not None else False

    logger.info(
        "Received extracted image semantic task submission",
        extra=build_log_extra(
            "extracted_image.semantic_task_create.started",
            image_id=image_id,
            request_id=request_id,
            requested_model=requested_model or "<default>",
            overwrite_existing_snapshot=overwrite_existing_snapshot,
        ),
    )
    try:
        submission = create_or_reuse_extracted_image_semantic_task(
            session,
            extracted_image=extracted_image,
            requested_model=requested_model,
            request_id=request_id,
            overwrite_existing_snapshot=overwrite_existing_snapshot,
        )
    except IntegrityError as exc:
        session.rollback()
        logger.exception(
            "Failed to create extracted image semantic task",
            extra=build_log_extra(
                "extracted_image.semantic_task_create.failed",
                image_id=image_id,
                request_id=request_id,
                overwrite_existing_snapshot=overwrite_existing_snapshot,
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
            overwrite_existing_snapshot=submission.task.overwrite_existing_snapshot,
        ),
    )
    return _to_task_read(submission.task, reused=submission.reused)


@router.get("/{image_id}/semantic-description", response_model=ExtractedImageSemanticImageResultRead)
def get_extracted_image_semantic_result(
    image_id: int,
    session: Session = Depends(get_session),
) -> ExtractedImageSemanticImageResultRead:
    """返回图片最近一次语义任务的聚合结果视图。"""

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
    """更新抽取图片的已提交字段。

    失败语义：
    - 当图片哈希冲突时返回 409。
    """

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
    """删除抽取图片记录。"""

    extracted_image = get_extracted_image_or_404(image_id, session)
    session.delete(extracted_image)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _to_task_read(task: ExtractedImageSemanticTask, *, reused: bool = False) -> ExtractedImageSemanticTaskRead:
    """把抽取图片语义任务实体转换为 API 读取视图。"""

    return ExtractedImageSemanticTaskRead(
        id=task.id,
        image_id=task.extracted_image_id,
        status=task.status,
        requested_model=task.requested_model,
        target_model=task.target_model,
        overwrite_existing_snapshot=task.overwrite_existing_snapshot,
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
    """把抽取图片语义任务转换为按图片读取的聚合结果视图。"""

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
