import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..dependencies import (
    get_active_document_or_404,
    get_file_convert_service_client,
    get_file_or_404,
)
from ...database import get_session
from ...services import FileConvertServiceClient
from ...services.extracted_image_persistence_service import (
    ExtractedImagePersistenceError,
    persist_extracted_images,
)

router = APIRouter(prefix="/converters", tags=["converters"])
logger = logging.getLogger(__name__)


class ConverterAvailabilityRead(BaseModel):
    available: bool
    service: str
    health_path: str | None = None
    error: str | None = None


class PdfToMarkdownConvertRequest(BaseModel):
    document_id: UUID


class PdfToMarkdownConvertRead(BaseModel):
    document_id: UUID
    storage_key: str
    markdown: str
    image_hashes: dict[str, str] = Field(default_factory=dict)


@router.get("/availability", response_model=ConverterAvailabilityRead)
def get_converter_availability(
    client: FileConvertServiceClient = Depends(get_file_convert_service_client),
) -> ConverterAvailabilityRead:
    logger.info("Checking file-convert-service availability")
    available, error = client.check_availability()
    if available:
        return ConverterAvailabilityRead(
            available=True,
            service="file-convert-service",
            health_path="/health",
        )

    logger.warning("file-convert-service is unavailable: %s", error)
    return ConverterAvailabilityRead(
        available=False,
        service="file-convert-service",
        error=error,
    )


@router.post("/pdf-to-markdown", response_model=PdfToMarkdownConvertRead)
def convert_pdf_to_markdown(
    payload: PdfToMarkdownConvertRequest,
    session: Session = Depends(get_session),
    client: FileConvertServiceClient = Depends(get_file_convert_service_client),
) -> PdfToMarkdownConvertRead:
    document = get_active_document_or_404(payload.document_id, session)
    if document.file_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document file not found")

    file_record = get_file_or_404(document.file_id, session)
    if file_record.extension.lower() != ".pdf":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only PDF document is supported")

    convert_result, error = client.convert_pdf_to_markdown(storage_key=file_record.storage_key)
    if error is not None:
        logger.warning(
            "file-convert-service convert failed, document_id=%s, storage_key=%s, error=%s",
            payload.document_id,
            file_record.storage_key,
            error,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"file-convert-service convert failed: {error}",
        )

    if convert_result is not None:
        try:
            persist_extracted_images(session, uploaded_images=convert_result.uploaded_images)
        except ExtractedImagePersistenceError as exc:
            logger.exception(
                "Failed to persist extracted images after convert, document_id=%s, storage_key=%s",
                payload.document_id,
                file_record.storage_key,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to persist extracted images",
            ) from exc

    return PdfToMarkdownConvertRead(
        document_id=document.id,
        storage_key=file_record.storage_key,
        markdown=convert_result.markdown if convert_result is not None else "",
        image_hashes=convert_result.image_hashes if convert_result is not None else {},
    )
