"""文档上传编排服务。

职责：
1. 解析上传输入并规范化元数据。
2. 按文件哈希进行去重复用。
3. 对“记录存在但对象丢失”的场景执行修复上传。
4. 处理并发冲突下的回滚与 MinIO 清理。

说明：
- 本模块是上传路由的业务编排层，路由只负责 HTTP 入参与权限校验。
- 上传新对象时统一写入文档前缀（documents/）。
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from minio.error import S3Error
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..models import Document, FileRecord
from .minio_storage import MinioStorage, StoredObjectRef

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadInput:
    """上传输入的规范化结果。"""

    file_content: bytes
    content_type: str
    extension: str
    file_hash: str
    document_name: str
    parsed_extra_info: dict[str, Any] | None


@dataclass
class UploadFileResolution:
    """文件定位决议。

    - `created_new_file=True`：本次新建了 FileRecord 和 MinIO 对象。
    - `repaired_existing_file=True`：复用了 FileRecord，但修复了缺失对象。
    """

    file_record: FileRecord
    uploaded_storage_ref: StoredObjectRef | None
    created_new_file: bool
    repaired_existing_file: bool


def _parse_extra_info(extra_info: str | None) -> dict[str, Any] | None:
    """解析 extra_info JSON，并保证其为对象类型。"""
    if extra_info is None or extra_info.strip() == "":
        return None
    try:
        parsed = json.loads(extra_info)
    except json.JSONDecodeError as exc:
        logger.warning("Invalid extra_info JSON payload")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="extra_info is not valid JSON",
        ) from exc
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        logger.warning("extra_info is not a JSON object")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="extra_info must be a JSON object",
        )
    return parsed


def _find_file_by_hash(file_hash: str, session: Session) -> FileRecord | None:
    """按文件哈希查找已存在文件记录。"""
    statement = select(FileRecord).where(FileRecord.file_hash == file_hash)
    return session.exec(statement).first()


def _cleanup_uploaded_object(storage: MinioStorage, storage_ref: StoredObjectRef) -> None:
    """在数据库写入失败时尝试清理刚上传的对象。

    这是并发冲突/事务失败场景下的补偿动作，避免无主对象堆积。
    """
    try:
        storage.remove_object(storage_ref.storage_key, bucket=storage_ref.bucket)
    except S3Error:
        logger.warning(
            "Failed to cleanup uploaded object from MinIO, storage_bucket=%s, storage_key=%s",
            storage_ref.bucket,
            storage_ref.storage_key,
        )


async def prepare_upload_input(
    *,
    name: str | None,
    extra_info: str | None,
    upload_file: UploadFile,
) -> UploadInput:
    """读取上传文件并构建规范化输入。"""
    file_content = await upload_file.read()
    if not file_content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    content_type = upload_file.content_type or "application/octet-stream"
    extension = Path(upload_file.filename or "").suffix.lower()
    file_hash = hashlib.sha256(file_content).hexdigest()
    document_name = (name or "").strip() or upload_file.filename or "uploaded-file"
    parsed_extra_info = _parse_extra_info(extra_info)

    return UploadInput(
        file_content=file_content,
        content_type=content_type,
        extension=extension,
        file_hash=file_hash,
        document_name=document_name,
        parsed_extra_info=parsed_extra_info,
    )


def _repair_missing_object_if_needed(
    *,
    file_record: FileRecord,
    storage: MinioStorage,
    upload_input: UploadInput,
) -> StoredObjectRef | None:
    """检测并修复“记录存在但对象丢失”的异常状态。"""
    if storage.object_exists(file_record.storage_key, bucket=file_record.storage_bucket):
        return None

    logger.warning(
        "Existing file object missing, repairing file_id=%s, storage_bucket=%s, old_storage_key=%s",
        file_record.id,
        file_record.storage_bucket,
        file_record.storage_key,
    )

    repaired_storage_ref = storage.upload_document_bytes(
        upload_input.file_content,
        content_type=upload_input.content_type,
        extension=upload_input.extension,
    )

    # 修复成功后，FileRecord 需指向新的可用对象。
    file_record.storage_bucket = repaired_storage_ref.bucket
    file_record.storage_key = repaired_storage_ref.storage_key
    file_record.file_size = len(upload_input.file_content)
    file_record.content_type = upload_input.content_type
    file_record.extension = upload_input.extension
    return repaired_storage_ref


def resolve_file_record(
    *,
    session: Session,
    storage: MinioStorage,
    upload_input: UploadInput,
    project_id: UUID,
) -> UploadFileResolution:
    """决策文件去重分支：新建 / 复用 / 缺失修复。"""
    file_record = _find_file_by_hash(upload_input.file_hash, session)
    if file_record is None:
        try:
            uploaded_storage_ref = storage.upload_document_bytes(
                upload_input.file_content,
                content_type=upload_input.content_type,
                extension=upload_input.extension,
            )
        except S3Error as exc:
            logger.exception("MinIO upload failed for project_id=%s", project_id)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"MinIO upload failed: {exc.code}",
            ) from exc

        file_record = FileRecord(
            file_hash=upload_input.file_hash,
            storage_bucket=uploaded_storage_ref.bucket,
            storage_key=uploaded_storage_ref.storage_key,
            file_size=len(upload_input.file_content),
            content_type=upload_input.content_type,
            extension=upload_input.extension,
        )
        return UploadFileResolution(
            file_record=file_record,
            uploaded_storage_ref=uploaded_storage_ref,
            created_new_file=True,
            repaired_existing_file=False,
        )

    try:
        repaired_storage_ref = _repair_missing_object_if_needed(
            file_record=file_record,
            storage=storage,
            upload_input=upload_input,
        )
    except S3Error as exc:
        logger.exception("Failed to verify/repair MinIO object for existing file_id=%s", file_record.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MinIO check failed: {exc.code}",
        ) from exc

    if repaired_storage_ref is not None:
        logger.info("Repaired missing MinIO object for file_id=%s", file_record.id)
        return UploadFileResolution(
            file_record=file_record,
            uploaded_storage_ref=repaired_storage_ref,
            created_new_file=False,
            repaired_existing_file=True,
        )

    logger.info("Reusing existing file_id=%s for hash=%s", file_record.id, upload_input.file_hash)
    return UploadFileResolution(
        file_record=file_record,
        uploaded_storage_ref=None,
        created_new_file=False,
        repaired_existing_file=False,
    )


def persist_document(
    *,
    session: Session,
    storage: MinioStorage,
    upload_input: UploadInput,
    resolution: UploadFileResolution,
    project_id: UUID,
    software_id: UUID | None,
    description: str,
) -> Document:
    """持久化文档记录并处理并发冲突补偿。"""
    file_record = resolution.file_record
    document = Document(
        file_id=file_record.id,
        project_id=project_id,
        software_id=software_id,
        name=upload_input.document_name,
        description=description,
        extra_info=upload_input.parsed_extra_info,
    )

    try:
        if resolution.created_new_file:
            session.add(file_record)
            try:
                session.flush()
            except IntegrityError:
                # 并发上传同一文件时，可能在 flush 阶段命中 file_hash 唯一约束。
                # 处理策略：回滚事务 -> 清理本次对象 -> 复用已存在 FileRecord。
                session.rollback()
                if resolution.uploaded_storage_ref is not None:
                    _cleanup_uploaded_object(storage, resolution.uploaded_storage_ref)
                existing_file = _find_file_by_hash(upload_input.file_hash, session)
                if existing_file is None:
                    logger.exception("File hash conflict but existing file not found, hash=%s", upload_input.file_hash)
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="File dedupe conflict, please retry")
                file_record = existing_file
                document.file_id = file_record.id
        elif resolution.repaired_existing_file:
            # 修复路径下需要更新 FileRecord 的存储定位信息。
            session.add(file_record)

        session.add(document)
        session.commit()
        session.refresh(document)
    except IntegrityError as exc:
        session.rollback()
        if resolution.uploaded_storage_ref is not None:
            _cleanup_uploaded_object(storage, resolution.uploaded_storage_ref)
        logger.exception("Failed to create document record for project_id=%s", project_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Document create conflict") from exc

    return document


async def upload_document_with_dedupe(
    *,
    session: Session,
    storage: MinioStorage,
    project_id: UUID,
    software_id: UUID | None,
    name: str | None,
    description: str,
    extra_info: str | None,
    upload_file: UploadFile,
) -> Document:
    """文档上传主编排：输入准备 -> 文件决议 -> 文档落库。"""
    upload_input = await prepare_upload_input(name=name, extra_info=extra_info, upload_file=upload_file)
    resolution = resolve_file_record(
        session=session,
        storage=storage,
        upload_input=upload_input,
        project_id=project_id,
    )
    return persist_document(
        session=session,
        storage=storage,
        upload_input=upload_input,
        resolution=resolution,
        project_id=project_id,
        software_id=software_id,
        description=description,
    )

