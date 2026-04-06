import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from minio.error import S3Error
from pydantic import BaseModel, Field

from ...core.logging import LEGACY_REQUEST_ID_HEADER, REQUEST_ID_HEADER, build_log_extra, get_request_id
from ...services import MarkerPdfToMarkdownConverter, MinioStorage
from ..dependencies import get_marker_pdf_to_markdown_converter, get_minio_storage

router = APIRouter(prefix="/internal/converters", tags=["converters"])
logger = logging.getLogger(__name__)

_SUPPORTED_PDF_MODEL = "marker"


class ConvertPdfToMarkdownRequest(BaseModel):
    storage_key: str
    model: str | None = None


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


def _resolve_pdf_model(model: str | None) -> str:
    if model is None:
        return _SUPPORTED_PDF_MODEL
    normalized_model = model.strip().lower()
    if not normalized_model:
        return _SUPPORTED_PDF_MODEL
    if normalized_model != _SUPPORTED_PDF_MODEL:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported model: {model.strip()}. Only '{_SUPPORTED_PDF_MODEL}' is supported",
        )
    return _SUPPORTED_PDF_MODEL


@router.post("/pdf-to-markdown", response_model=ConvertPdfToMarkdownRead)
def convert_pdf_to_markdown_from_storage(
    payload: ConvertPdfToMarkdownRequest,
    request: Request,
    storage: MinioStorage = Depends(get_minio_storage),
    converter: MarkerPdfToMarkdownConverter = Depends(get_marker_pdf_to_markdown_converter),
) -> ConvertPdfToMarkdownRead:
    request_id = get_request_id()
    trace_task_id = request.headers.get(LEGACY_REQUEST_ID_HEADER) or request_id

    storage_key = payload.storage_key.strip()
    if not storage_key:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="storage_key is required")
    if not storage_key.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only PDF file is supported")

    target_model = _resolve_pdf_model(payload.model)

    if not storage.object_exists(storage_key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File object not found")

    logger.info(
        "Start PDF to markdown conversion",
        extra=build_log_extra(
            "pdf_to_markdown.started",
            request_id=request_id,
            task_id=trace_task_id,
            storage_key=storage_key,
            model=target_model,
            request_header=request.headers.get(REQUEST_ID_HEADER),
        ),
    )

    try:
        pdf_payload = storage.download_bytes(storage_key)
    except S3Error as exc:
        logger.exception(
            "Failed to download PDF from MinIO",
            extra=build_log_extra(
                "pdf_to_markdown.download_failed",
                request_id=request_id,
                task_id=trace_task_id,
                storage_key=storage_key,
                model=target_model,
                error_code=exc.code,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO download failed: {exc.code}",
        ) from exc

    try:
        convert_result = converter.convert(pdf_payload)
    except RuntimeError as exc:
        logger.exception(
            "Marker converter is unavailable",
            extra=build_log_extra(
                "pdf_to_markdown.converter_unavailable",
                request_id=request_id,
                task_id=trace_task_id,
                model=target_model,
            ),
        )
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Failed to convert PDF to markdown",
            extra=build_log_extra(
                "pdf_to_markdown.failed",
                request_id=request_id,
                task_id=trace_task_id,
                storage_key=storage_key,
                model=target_model,
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF conversion failed: {exc}",
        ) from exc

    logger.info(
        "Finished PDF to markdown conversion",
        extra=build_log_extra(
            "pdf_to_markdown.succeeded",
            request_id=request_id,
            task_id=trace_task_id,
            storage_key=storage_key,
            model=target_model,
            uploaded_image_count=len(convert_result.uploaded_images),
        ),
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
