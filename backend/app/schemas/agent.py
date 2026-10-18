from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.chat import Citation


class AgentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    knowledge_base_id: int = Field(gt=0)
    task: str = Field(min_length=1, max_length=4000)
    conversation_id: UUID | None = None
    request_id: UUID | None = None
    regenerate_turn: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_session(self):
        if self.conversation_id is None and (self.request_id is not None or self.regenerate_turn is not None):
            raise ValueError("conversation_id is required for request_id or regeneration")
        return self



class AgentResponse(BaseModel):
    knowledge_base_id: int
    answer: str
    citations: list[Citation]
    tool_calls: int
