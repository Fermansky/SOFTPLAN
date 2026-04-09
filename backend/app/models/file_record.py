"""文件记录模型。

职责：
1. 保存上传文件在对象存储中的定位信息与基础元数据。
2. 为文档、任务等上层实体提供可复用的文件引用。

说明：
- `file_hash` 全局唯一，用于文件级去重复用。
- 模型只描述对象信息，不承载文档语义。
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BIGINT, Column, Text
from sqlmodel import Field, SQLModel

from .common import utc_now


class FileRecordBase(SQLModel):
    """文件记录共享字段。"""

    file_hash: str = Field(sa_column=Column(Text, nullable=False, unique=True, index=True))
    storage_bucket: str = Field(sa_column=Column(Text, nullable=False))
    storage_key: str = Field(sa_column=Column(Text, nullable=False))
    file_size: int = Field(sa_column=Column(BIGINT, nullable=False))
    content_type: str = Field(default="application/octet-stream", sa_column=Column(Text, nullable=False))
    extension: str = Field(default="", sa_column=Column(Text, nullable=False))


class FileRecord(FileRecordBase, table=True):
    """文件记录持久化实体。"""

    __tablename__ = "files"

    id: UUID = Field(default_factory=uuid4, primary_key=True, index=True)
    created_at: datetime = Field(default_factory=utc_now, nullable=False)


class FileRecordCreate(FileRecordBase):
    """创建文件记录时使用的输入模型。"""

    pass


class FileRecordRead(FileRecordBase):
    """对外返回的文件记录读取模型。"""

    id: UUID
    created_at: datetime
