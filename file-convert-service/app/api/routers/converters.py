import logging

from fastapi import APIRouter, Depends, HTTPException, status
from minio.error import S3Error
from pydantic import BaseModel

from ..dependencies import get_marker_pdf_to_markdown_converter, get_minio_storage
from ...services import MarkerPdfToMarkdownConverter, MinioStorage

router = APIRouter(prefix="/internal/converters", tags=["converters"])
logger = logging.getLogger(__name__)


class ConvertPdfToMarkdownRequest(BaseModel):
    storage_key: str


class ConvertPdfToMarkdownRead(BaseModel):
    storage_key: str
    markdown: str


@router.post("/pdf-to-markdown", response_model=ConvertPdfToMarkdownRead)
def convert_pdf_to_markdown_from_storage(
    payload: ConvertPdfToMarkdownRequest,
    storage: MinioStorage = Depends(get_minio_storage),
    converter: MarkerPdfToMarkdownConverter = Depends(get_marker_pdf_to_markdown_converter),
) -> ConvertPdfToMarkdownRead:
    storage_key = payload.storage_key.strip()
    if not storage_key:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="storage_key is required")
    if not storage_key.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only PDF file is supported")

    if not storage.object_exists(storage_key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File object not found")

    try:
        pdf_payload = storage.download_bytes(storage_key)
    except S3Error as exc:
        logger.exception("Failed to download PDF from MinIO, storage_key=%s", storage_key)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO download failed: {exc.code}",
        ) from exc

    try:
        markdown = converter.convert(pdf_payload)
    except RuntimeError as exc:
        logger.exception("Marker converter is unavailable")
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to convert PDF to markdown, storage_key=%s", storage_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF conversion failed: {exc}",
        ) from exc

    return ConvertPdfToMarkdownRead(storage_key=storage_key, markdown=markdown)
