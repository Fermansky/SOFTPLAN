import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from minio.error import S3Error
from pydantic import BaseModel, Field

from ..dependencies import get_marker_pdf_to_markdown_converter, get_minio_storage
from ...services import MarkerPdfToMarkdownConverter, MinioStorage

router = APIRouter(prefix="/internal/converters", tags=["converters"])
logger = logging.getLogger(__name__)


class ConvertPdfToMarkdownRequest(BaseModel):
    storage_key: str


class UploadedImageRead(BaseModel):
    source_key: str
    file_hash: str
    storage_bucket: str
    storage_key: str
    file_size: int
    content_type: str
    extension: str | None = None
    width: int | None = None
    height: int | None = None


class ConvertPdfToMarkdownRead(BaseModel):
    storage_key: str
    markdown: str
    image_hashes: dict[str, str] = Field(default_factory=dict)
    uploaded_images: list[UploadedImageRead] = Field(default_factory=list)


@router.post("/pdf-to-markdown", response_model=ConvertPdfToMarkdownRead)
def convert_pdf_to_markdown_from_storage(
    payload: ConvertPdfToMarkdownRequest,
    request: Request,
    storage: MinioStorage = Depends(get_minio_storage),
    converter: MarkerPdfToMarkdownConverter = Depends(get_marker_pdf_to_markdown_converter),
) -> ConvertPdfToMarkdownRead:
    trace_task_id = request.headers.get("X-Convert-Task-Id")

    storage_key = payload.storage_key.strip()
    if not storage_key:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="storage_key is required")
    if not storage_key.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only PDF file is supported")

    if not storage.object_exists(storage_key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File object not found")

    logger.info(
        "Start PDF to markdown conversion, storage_key=%s, task_id=%s",
        storage_key,
        trace_task_id,
    )

    try:
        pdf_payload = storage.download_bytes(storage_key)
    except S3Error as exc:
        logger.exception("Failed to download PDF from MinIO, storage_key=%s, task_id=%s", storage_key, trace_task_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO download failed: {exc.code}",
        ) from exc

    try:
        convert_result = converter.convert(pdf_payload)
    except RuntimeError as exc:
        logger.exception("Marker converter is unavailable, task_id=%s", trace_task_id)
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to convert PDF to markdown, storage_key=%s, task_id=%s", storage_key, trace_task_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF conversion failed: {exc}",
        ) from exc

    logger.info(
        "Finished PDF to markdown conversion, storage_key=%s, task_id=%s, images=%s",
        storage_key,
        trace_task_id,
        len(convert_result.uploaded_images),
    )

    return ConvertPdfToMarkdownRead(
        storage_key=storage_key,
        markdown=convert_result.markdown,
        image_hashes=convert_result.image_hashes,
        uploaded_images=[
            UploadedImageRead(
                source_key=item.source_key,
                file_hash=item.file_hash,
                storage_bucket=item.storage_bucket,
                storage_key=item.storage_key,
                file_size=item.file_size,
                content_type=item.content_type,
                extension=item.extension,
                width=item.width,
                height=item.height,
            )
            for item in convert_result.uploaded_images
        ],
    )
