from typing import Literal

from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    knowledge_base_id: int = Field(gt=0)
    question: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    conversation_id: UUID | None = None
    request_id: UUID | None = None
    regenerate_turn: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_session(self):
        if self.conversation_id is None and (self.request_id is not None or self.regenerate_turn is not None):
            raise ValueError("conversation_id is required for request_id or regeneration")
        return self



class Citation(BaseModel):
    id: int
    knowledge_base_id: int
    document_id: str
    document_name: str
    page: int | None
    chunk_id: str
    content: str
    score: float


ChatStatus = Literal["answered", "empty_knowledge_base", "no_relevant_content", "insufficient_evidence"]


class ChatResponse(BaseModel):
    knowledge_base_id: int
    answer: str
    citations: list[Citation]
    status: ChatStatus
