import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from minio.error import S3Error
from pydantic import BaseModel

from ..dependencies import get_minio_storage
from ...services import MinioStorage

router = APIRouter(prefix="/internal/storage", tags=["storage"])
logger = logging.getLogger(__name__)


class UploadObjectRead(BaseModel):
    bucket: str
    storage_key: str


@router.post("/objects", response_model=UploadObjectRead, status_code=status.HTTP_201_CREATED)
async def upload_object(
    file: UploadFile = File(...),
    storage: MinioStorage = Depends(get_minio_storage),
) -> UploadObjectRead:
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    extension = Path(file.filename or "").suffix.lower()
    content_type = file.content_type or "application/octet-stream"
    try:
        storage_key = storage.upload_bytes(payload, content_type=content_type, extension=extension)
    except S3Error as exc:
        logger.exception("Failed to upload file to MinIO")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO upload failed: {exc.code}",
        ) from exc

    return UploadObjectRead(bucket=storage.bucket, storage_key=storage_key)


@router.get("/objects/{storage_key:path}")
def download_object(
    storage_key: str,
    storage: MinioStorage = Depends(get_minio_storage),
) -> Response:
    if not storage.object_exists(storage_key):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File object not found")

    try:
        payload = storage.download_bytes(storage_key)
    except S3Error as exc:
        logger.exception("Failed to download file from MinIO, storage_key=%s", storage_key)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO download failed: {exc.code}",
        ) from exc

    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"X-Storage-Key": storage_key, "Content-Length": str(len(payload))},
    )
