"""抽取图片元数据持久化服务。

职责：
1. 将 file-convert-service 返回的图片元数据批量落库到 extracted_images。
2. 以 `file_hash` 作为唯一键执行 Dedup Reuse。

说明：
- 本模块只负责元数据持久化，不负责语义解析任务分发。
- 冲突时采用 `ON CONFLICT(file_hash) DO NOTHING`，由后续查询阶段复用现有记录。
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
    """抽取图片元数据持久化失败。"""



def persist_extracted_images(session: Session, *, uploaded_images: Sequence[UploadedImageMetadata]) -> None:
    """批量落库抽取图片元数据。

    约束：
    - 按 `file_hash` 去重，冲突时忽略新行，不中断主流程。

    副作用：
    - 会提交当前数据库事务。

    失败语义：
    - 任意数据库错误都会回滚并转为 `ExtractedImagePersistenceError`。
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
