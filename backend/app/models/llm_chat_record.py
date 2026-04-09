"""LLM 对话记录模型。

职责：
1. 保存一次 LLM 调用的请求快照、模型配置与响应结果。
2. 为审计、排障和成本统计提供结构化记录。

说明：
- 该模型记录事实结果，不负责请求重放或重试控制。
- `input_parts_snapshot` 保存调用时的输入片段快照，结构由调用方约定。
"""

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import JSON, Column, DateTime, Integer, Text
from sqlmodel import Field, SQLModel

from .common import utc_now


class LlmChatRecordStatus(str, Enum):
    """LLM 对话记录状态。"""

    succeeded = "succeeded"
    failed = "failed"


class LlmChatRecord(SQLModel, table=True):
    """LLM 调用持久化记录。

    单条记录覆盖一次请求的输入、模型解析结果、token 消耗、上游响应标识
    与失败信息，便于后续追踪调用链路。
    """

    __tablename__ = "llm_chat_records"

    id: int | None = Field(default=None, primary_key=True)
    status: LlmChatRecordStatus = Field(index=True)
    request_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True, index=True))
    caller_service: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    prompt: str = Field(sa_column=Column(Text, nullable=False))
    system_prompt: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    input_parts_snapshot: list[dict[str, object]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    input_part_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    image_part_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    llm_config_id: UUID | None = Field(default=None, nullable=True, index=True)
    llm_config_code: str | None = Field(default=None, sa_column=Column(Text, nullable=True, index=True))
    requested_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    resolved_model: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    temperature: float | None = Field(default=None)
    max_tokens: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    prompt_tokens: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    completion_tokens: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    total_tokens: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
    response_text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    upstream_base_url: str = Field(sa_column=Column(Text, nullable=False))
    upstream_response_request_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    upstream_response_id: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    completed_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    duration_ms: int = Field(default=0, sa_column=Column(Integer, nullable=False, default=0))
