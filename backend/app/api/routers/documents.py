"""文档路由。

职责：
1. 提供文档的上传、创建、列表、详情、下载、更新与逻辑删除接口。
2. 处理文档与项目、软件、文件记录之间的基础关联校验。

说明：
- 下载接口会访问对象存储读取文件内容。
- 删除操作仅做软删除，不清理对象存储文件。
"""

import logging
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from minio.error import S3Error
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..dependencies import (
    get_active_document_or_404,
    get_active_project_or_404,
    get_file_or_404,
    get_minio_storage,
    get_software_or_404,
)
from ...database import get_session
from ...models import Document, DocumentCreate, DocumentRead, DocumentUpdate
from ...models.common import utc_now
from ...services import MinioStorage
from ...services.document_upload_service import upload_document_with_dedupe

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


def _build_download_filename(name: str, extension: str) -> str:
    """根据文档名称和扩展名构造下载文件名。"""

    filename = name.strip() or "document"
    normalized_extension = extension.strip()
    if normalized_extension and not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"
    if normalized_extension and not filename.lower().endswith(normalized_extension.lower()):
        filename = f"{filename}{normalized_extension}"
    return filename


def _build_content_disposition(filename: str) -> str:
    """构造兼容 ASCII 与 UTF-8 文件名的下载响应头。"""

    ascii_filename = filename.encode("ascii", "ignore").decode("ascii").strip() or "document"
    encoded_filename = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{encoded_filename}"


def _build_documents_query(
    *,
    offset: int,
    limit: int,
    project_id: UUID | None = None,
    software_id: UUID | None = None,
):
    """构造文档列表查询，并默认过滤已软删除记录。"""

    statement = select(Document).where(Document.deleted_at.is_(None))
    if project_id is not None:
        statement = statement.where(Document.project_id == project_id)
    if software_id is not None:
        statement = statement.where(Document.software_id == software_id)
    return statement.order_by(Document.created_at.desc()).offset(offset).limit(limit)


@router.post("/upload", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    project_id: UUID = Form(...),
    software_id: UUID | None = Form(default=None),
    name: str | None = Form(default=None),
    description: str = Form(default=""),
    extra_info: str | None = Form(default=None),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    storage: MinioStorage = Depends(get_minio_storage),
) -> Document:
    """上传文件并创建文档。

    约束：
    - 项目必须存在；若传入软件 ID，则软件也必须存在。

    副作用：
    - 读取上传文件。
    - 调用文档上传服务写入对象存储、文件记录和文档记录。
    """

    logger.info("Uploading document for project_id=%s, software_id=%s", project_id, software_id)
    get_active_project_or_404(project_id, session)
    if software_id is not None:
        get_software_or_404(software_id, session)

    document = await upload_document_with_dedupe(
        session=session,
        storage=storage,
        project_id=project_id,
        software_id=software_id,
        name=name,
        description=description,
        extra_info=extra_info,
        upload_file=file,
    )

    logger.info(
        "Document uploaded successfully, document_id=%s, file_id=%s, project_id=%s",
        document.id,
        document.file_id,
        project_id,
    )
    return document


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create_document(payload: DocumentCreate, session: Session = Depends(get_session)) -> Document:
    """按 JSON 负载直接创建文档记录。

    约束：
    - 关联的项目、软件、文件记录若存在引用，必须先校验存在性。

    失败语义：
    - 写库冲突时返回 409。
    """

    logger.info("Creating document by JSON payload, project_id=%s, file_id=%s", payload.project_id, payload.file_id)
    get_active_project_or_404(payload.project_id, session)
    if payload.software_id is not None:
        get_software_or_404(payload.software_id, session)
    if payload.file_id is not None:
        get_file_or_404(payload.file_id, session)

    document = Document(**payload.model_dump())
    try:
        session.add(document)
        session.commit()
        session.refresh(document)
    except IntegrityError as exc:
        session.rollback()
        logger.exception("Failed to create document by JSON payload, project_id=%s", payload.project_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document create conflict") from exc

    logger.info("Document created by JSON payload, document_id=%s", document.id)
    return document


@router.get("", response_model=list[DocumentRead])
def list_documents(
    session: Session = Depends(get_session),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    project_id: UUID | None = None,
    software_id: UUID | None = None,
) -> list[Document]:
    """分页返回未软删除文档列表，可按项目或软件筛选。"""

    logger.info(
        "Listing documents, project_id=%s, software_id=%s, offset=%s, limit=%s",
        project_id,
        software_id,
        offset,
        limit,
    )
    statement = _build_documents_query(
        offset=offset,
        limit=limit,
        project_id=project_id,
        software_id=software_id,
    )
    return list(session.exec(statement).all())


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(document_id: UUID, session: Session = Depends(get_session)) -> Document:
    """返回单个未软删除文档详情，未命中时返回 404。"""

    logger.info("Fetching document detail, document_id=%s", document_id)
    return get_active_document_or_404(document_id, session)


@router.get("/{document_id}/download")
def download_document(
    document_id: UUID,
    session: Session = Depends(get_session),
    storage: MinioStorage = Depends(get_minio_storage),
) -> Response:
    """下载文档对应的原始文件内容。

    副作用：
    - 访问对象存储读取二进制文件。

    失败语义：
    - 文档没有绑定文件或文件记录缺失时返回 404。
    - 对象存储缺失对象时返回 404。
    - 其他对象存储下载错误返回 502。
    """

    logger.info("Downloading document file, document_id=%s", document_id)
    document = get_active_document_or_404(document_id, session)
    if document.file_id is None:
        logger.warning("Document has no file_id, document_id=%s", document_id)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document file not found")

    file_record = get_file_or_404(document.file_id, session)
    try:
        payload = storage.download_bytes(file_record.storage_key, bucket=file_record.storage_bucket)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}:
            logger.warning(
                "Document file object missing in MinIO, document_id=%s, file_id=%s, storage_bucket=%s, storage_key=%s",
                document_id,
                file_record.id,
                file_record.storage_bucket,
                file_record.storage_key,
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File object not found in storage") from exc
        logger.exception("Failed to download document from MinIO, document_id=%s, file_id=%s", document_id, file_record.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO download failed: {exc.code}",
        ) from exc

    filename = _build_download_filename(document.name, file_record.extension)
    headers = {
        "Content-Disposition": _build_content_disposition(filename),
        "Content-Length": str(len(payload)),
    }
    logger.info(
        "Document file downloaded successfully, document_id=%s, file_id=%s, size=%s",
        document_id,
        file_record.id,
        len(payload),
    )
    return Response(content=payload, media_type=file_record.content_type, headers=headers)


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: UUID, payload: DocumentUpdate, session: Session = Depends(get_session)
) -> Document:
    """更新文档的已提交字段。

    约束：
    - 新引用的项目、软件、文件记录必须存在。

    副作用：
    - 更新文档字段与 `updated_at` 并提交事务。

    失败语义：
    - 写库冲突时返回 409。
    """

    logger.info("Updating document, document_id=%s", document_id)
    document = get_active_document_or_404(document_id, session)

    update_data = payload.model_dump(exclude_unset=True)
    if "file_id" in update_data and update_data["file_id"] is not None:
        get_file_or_404(update_data["file_id"], session)
    if "project_id" in update_data and update_data["project_id"] is not None:
        get_active_project_or_404(update_data["project_id"], session)
    if "software_id" in update_data and update_data["software_id"] is not None:
        get_software_or_404(update_data["software_id"], session)

    for field_name, value in update_data.items():
        setattr(document, field_name, value)
    document.updated_at = utc_now()

    try:
        session.add(document)
        session.commit()
        session.refresh(document)
    except IntegrityError as exc:
        session.rollback()
        logger.exception("Failed to update document, document_id=%s", document_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document update conflict") from exc

    logger.info("Document updated, document_id=%s", document_id)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: UUID, session: Session = Depends(get_session)) -> Response:
    """逻辑删除文档。

    副作用：
    - 设置 `deleted_at` 与 `updated_at` 并提交事务。

    失败语义：
    - 写库冲突时返回 409。
    """

    logger.info("Deleting document logically, document_id=%s", document_id)
    document = get_active_document_or_404(document_id, session)
    now = utc_now()
    document.deleted_at = now
    document.updated_at = now
    try:
        session.add(document)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        logger.exception("Failed to delete document logically, document_id=%s", document_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document delete conflict") from exc

    logger.info("Document deleted logically, document_id=%s", document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
