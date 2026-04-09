"""抽取图片模型。

职责：
1. 保存从文档或其他流程中抽取出的图片对象信息。
2. 为图片语义任务与文档解析图片项提供稳定引用。

说明：
- `file_hash` 全局唯一，用于去重复用同一张图片对象。
- 兼容保留了历史语义描述字段，但语义快照以独立模型为准。
"""

from datetime import datetime

from sqlalchemy import BIGINT, CHAR, Column, DateTime, Identity, Integer, Text, func
from sqlmodel import Field, SQLModel

from .common import utc_now


class ExtractedImageBase(SQLModel):
    """抽取图片共享字段。"""

    file_hash: str = Field(min_length=64, max_length=64, sa_column=Column(CHAR(64), nullable=False, unique=True, index=True))
    storage_bucket: str = Field(sa_column=Column(Text, nullable=False))
    storage_key: str = Field(sa_column=Column(Text, nullable=False))
    file_size: int = Field(sa_column=Column(BIGINT, nullable=False))
    content_type: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    extension: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    width: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    height: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))


class ExtractedImageLegacySemanticSnapshot(SQLModel):
    """历史遗留的图片语义字段视图。

    保留旧接口所需的语义描述字段，避免读取模型直接暴露表结构演进细节。
    """

    semantic_description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    semantic_description_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    semantic_description_updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class ExtractedImage(ExtractedImageBase, table=True):
    """抽取图片持久化实体。

    记录图片对象在存储中的位置和基础元数据，并兼容保留历史语义描述字段。
    """

    __tablename__ = "extracted_images"

    id: int | None = Field(
        default=None,
        sa_column=Column(BIGINT, Identity(always=True), primary_key=True, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    semantic_description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    semantic_description_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    semantic_description_updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class ExtractedImageCreate(ExtractedImageBase):
    """创建抽取图片时使用的输入模型。"""

    pass


class ExtractedImageUpdate(SQLModel):
    """更新抽取图片时使用的局部修改模型。"""

    file_hash: str | None = Field(default=None, min_length=64, max_length=64)
    storage_bucket: str | None = None
    storage_key: str | None = None
    file_size: int | None = None
    content_type: str | None = None
    extension: str | None = None
    width: int | None = None
    height: int | None = None


class ExtractedImageRead(ExtractedImageBase, ExtractedImageLegacySemanticSnapshot):
    """对外返回的抽取图片读取模型。"""

    id: int
    created_at: datetime
