from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

Mode = Literal["chat", "agent"]
MessageStatus = Literal["pending", "completed", "failed", "cancelled", "interrupted"]


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    knowledge_base_id: int = Field(gt=0, strict=True)
    title: str = Field(min_length=1, max_length=100)
    mode: Mode


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=100)


class Pagination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1_000_000)


class ConversationQuery(Pagination):
    knowledge_base_id: int | None = Field(default=None, gt=0)
    mode: Mode | None = None


class ScopeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_base_id: int | None = Field(default=None, gt=0)


class MessageQuery(Pagination):
    knowledge_base_id: int | None = Field(default=None, gt=0)


class Timestamped(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_utc(self, value: datetime) -> str:
        return value.replace(tzinfo=timezone.utc).isoformat()


class ConversationRead(Timestamped):
    id: UUID
    knowledge_base_id: int
    title: str
    mode: Mode
    summary: str
    summary_through_turn: int
    revision: int


class CitationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(gt=0, strict=True)
    knowledge_base_id: int = Field(gt=0, strict=True)
    document_id: UUID
    document_name: str = Field(min_length=1, max_length=255)
    page: int | None = Field(default=None, ge=1, strict=True)
    chunk_id: UUID
    content: str = Field(min_length=1, max_length=2_000_000)
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)


class ResultMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    chat_status: Literal["answered", "empty_knowledge_base", "no_relevant_content", "insufficient_evidence"] | None = None
    tool_calls: int | None = Field(default=None, ge=0, le=32, strict=True)


class MessageWrite(BaseModel):
    """Internal service input only; no public message-write endpoint in Step 2A."""
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(max_length=32768)
    turn_no: int = Field(ge=1, strict=True)
    attempt_no: int = Field(default=1, ge=1, strict=True)
    request_id: UUID | None = None
    status: MessageStatus = "completed"
    citations: list[CitationSnapshot] = Field(default_factory=list, max_length=5000)
    result_metadata: ResultMetadata = Field(default_factory=ResultMetadata)
    error_code: str | None = Field(default=None, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")

    @model_validator(mode="after")
    def validate_state(self):
        if self.status == "completed" and not self.content.strip():
            raise ValueError("completed messages must contain text")
        if self.role == "user" and (self.status != "completed" or self.citations or self.error_code or self.result_metadata.model_dump(exclude_none=True)):
            raise ValueError("user messages cannot carry assistant state")
        if self.status != "completed" and (self.content or self.citations or self.result_metadata.model_dump(exclude_none=True)):
            raise ValueError("unfinished answers cannot contain verified output")
        if self.status in {"completed", "pending"} and self.error_code:
            raise ValueError("unexpected error code")
        if len({c.id for c in self.citations}) != len(self.citations):
            raise ValueError("duplicate citation IDs")
        return self


class MessageRead(Timestamped):
    id: UUID
    conversation_id: UUID
    role: Literal["user", "assistant"]
    content: str
    turn_no: int
    attempt_no: int
    request_id: UUID | None
    status: MessageStatus
    citations: list[CitationSnapshot]
    result_metadata: ResultMetadata
    error_code: str | None


class ConversationPage(BaseModel):
    items: list[ConversationRead]
    total: int
    limit: int
    offset: int


class MessagePage(BaseModel):
    items: list[MessageRead]
    total: int
    limit: int
    offset: int
