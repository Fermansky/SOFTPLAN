"""提取图片元数据落库服务。

职责：
- 将 file-convert-service 返回的图片元数据批量写入 extracted_images。
- 以 file_hash 做全局去重，冲突时静默复用。
"""

import logging
from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from ..models import ExtractedImage
from .file_convert_service import UploadedImageMetadata

logger = logging.getLogger(__name__)


class ExtractedImagePersistenceError(RuntimeError):
    """图片元数据持久化失败。"""



def persist_extracted_images(session: Session, *, uploaded_images: Sequence[UploadedImageMetadata]) -> None:
    """批量持久化提取图片元数据。

    约束：
    - 使用 `ON CONFLICT(file_hash) DO NOTHING` 保证幂等。
    - 非冲突类数据库异常统一回滚并抛出业务异常。
    """
    if not uploaded_images:
        return

    values = [
        {
            "file_hash": item.file_hash,
            "storage_bucket": item.storage_bucket,
            "storage_key": item.storage_key,
            "file_size": item.file_size,
            "content_type": item.content_type,
            "extension": item.extension,
            "width": item.width,
            "height": item.height,
        }
        for item in uploaded_images
    ]

    statement = insert(ExtractedImage).values(values)
    statement = statement.on_conflict_do_nothing(index_elements=[ExtractedImage.file_hash])

    try:
        session.exec(statement)
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        logger.exception("Failed to persist extracted images, count=%s", len(uploaded_images))
        raise ExtractedImagePersistenceError("Failed to persist extracted images") from exc

