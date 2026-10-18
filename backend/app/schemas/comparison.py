from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.chat import Citation, ChatResponse


class CompareRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    knowledge_base_id: int = Field(gt=0)
    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)
    include_answer: bool = False


class CompareRun(BaseModel):
    mode: Literal["basic", "reranked"]
    citations: list[Citation] = Field(default_factory=list)
    elapsed_ms: float
    answer: ChatResponse | None = None
    error_code: str | None = None
    error_message: str | None = None


class CompareResponse(BaseModel):
    runs: list[CompareRun]
    candidate_k: int
    score_kind: str = "vector_cosine_similarity"
