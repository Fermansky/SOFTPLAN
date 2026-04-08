from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, Column, DateTime, Integer, Text
from sqlmodel import Field, SQLModel

from .common import utc_now


class LlmChatRecordStatus(str, Enum):
    succeeded = "succeeded"
    failed = "failed"


class LlmChatRecord(SQLModel, table=True):
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
