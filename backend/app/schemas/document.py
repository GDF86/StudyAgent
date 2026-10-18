from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    knowledge_base_id: int
    filename: str
    file_type: str
    file_size: int
    status: str
    error_message: str | None
    chunk_count: int
    embedding_model: str | None
    embedding_dimension: int | None
    created_at: datetime

    @field_serializer("created_at")
    def serialize_utc(self, value: datetime) -> str:
        return value.replace(tzinfo=timezone.utc).isoformat()


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    document_id: str
    ordinal: int
    page: int | None
    content: str
    vector_id: str


class DocumentDetail(DocumentRead):
    chunks: list[ChunkRead]
    source: str
