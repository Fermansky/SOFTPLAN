import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..dependencies import get_extracted_image_or_404, get_llm_service_client, get_minio_storage
from ...database import get_session
from ...models import ExtractedImage, ExtractedImageCreate, ExtractedImageRead, ExtractedImageUpdate
from ...services import (
    ExtractedImageSemanticPromptError,
    LlmServiceClient,
    MinioStorage,
    describe_extracted_image_semantics,
    resolve_extracted_image_semantic_model,
)

router = APIRouter(prefix="/extracted-images", tags=["extracted-images"])
logger = logging.getLogger(__name__)


class ExtractedImageSemanticDescriptionRequest(BaseModel):
    request_id: str | None = None
    model: str | None = None


class ExtractedImageSemanticDescriptionRead(BaseModel):
    image_id: int
    description: str
    model: str
    request_id: str | None = None


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


@router.get("/{image_id}", response_model=ExtractedImageRead)
def get_extracted_image(image_id: int, session: Session = Depends(get_session)) -> ExtractedImage:
    return get_extracted_image_or_404(image_id, session)


@router.post("/{image_id}/semantic-description", response_model=ExtractedImageSemanticDescriptionRead)
def generate_extracted_image_semantic_description(
    image_id: int,
    payload: ExtractedImageSemanticDescriptionRequest | None = Body(default=None),
    session: Session = Depends(get_session),
    storage: MinioStorage = Depends(get_minio_storage),
    client: LlmServiceClient = Depends(get_llm_service_client),
) -> ExtractedImageSemanticDescriptionRead:
    extracted_image = get_extracted_image_or_404(image_id, session)
    request_id = payload.request_id if payload is not None else None
    requested_model = payload.model if payload is not None else None
    resolved_model = resolve_extracted_image_semantic_model(requested_model)

    logger.info(
        "Received extracted image semantic description request image_id=%s request_id=%s model=%s has_custom_model=%s",
        image_id,
        request_id,
        resolved_model or "<default>",
        bool(requested_model and requested_model.strip()),
    )

    try:
        result = describe_extracted_image_semantics(
            extracted_image=extracted_image,
            storage=storage,
            client=client,
            request_id=request_id,
            model=requested_model,
        )
    except ExtractedImageSemanticPromptError as exc:
        logger.warning(
            "Extracted image semantic prompt unavailable image_id=%s request_id=%s model=%s has_custom_model=%s error=%s",
            image_id,
            request_id,
            resolved_model or "<default>",
            bool(requested_model and requested_model.strip()),
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Semantic description prompt unavailable: {exc}",
        ) from exc

    return ExtractedImageSemanticDescriptionRead(
        image_id=result.image_id,
        description=result.description,
        model=result.model,
        request_id=result.request_id,
    )


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
