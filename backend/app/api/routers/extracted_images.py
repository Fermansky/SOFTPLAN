from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..dependencies import get_extracted_image_or_404
from ...database import get_session
from ...models import ExtractedImage, ExtractedImageCreate, ExtractedImageRead, ExtractedImageUpdate

router = APIRouter(prefix="/extracted-images", tags=["extracted-images"])


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
