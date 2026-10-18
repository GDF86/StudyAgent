from datetime import datetime, timezone

from uuid import uuid4

from sqlalchemy import DateTime, String, Text, ForeignKey, UniqueConstraint, CheckConstraint, Index, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Return UTC for storage in SQLite (which stores naive timestamps)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("knowledge_base_id", "content_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    knowledge_base_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(10))
    file_size: Mapped[int]
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="processing")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    ordinal: Mapped[int]
    page: Mapped[int | None] = mapped_column(nullable=True)
    content: Mapped[str] = mapped_column(Text)
    vector_id: Mapped[str] = mapped_column(String(60), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class Conversation(Base):
    """History container; summary fields are reserved, not generated in Step 2A."""
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("mode IN ('chat', 'agent')", name="ck_conversation_mode"),
        CheckConstraint("length(trim(title)) BETWEEN 1 AND 100", name="ck_conversation_title"),
        CheckConstraint("revision >= 0 AND summary_through_turn >= 0", name="ck_conversation_counters"),
        Index("ix_conversations_updated_id", "updated_at", "id"),
        Index("ix_conversations_kb_mode_updated", "knowledge_base_id", "mode", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    knowledge_base_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    summary_through_turn: Mapped[int] = mapped_column(default=0, nullable=False)
    revision: Mapped[int] = mapped_column(default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class Message(Base):
    """Stored user/final-assistant data, never internal reasoning or tool transcripts."""
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "turn_no", "attempt_no", "role", name="uq_message_turn_attempt_role"),
        UniqueConstraint("conversation_id", "request_id", "role", name="uq_message_request_role"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_message_role"),
        CheckConstraint("status IN ('pending', 'completed', 'failed', 'cancelled', 'interrupted')", name="ck_message_status"),
        CheckConstraint("turn_no >= 1 AND attempt_no >= 1", name="ck_message_order"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    turn_no: Mapped[int] = mapped_column(nullable=False)
    attempt_no: Mapped[int] = mapped_column(default=1, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="completed", nullable=False)
    citations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    result_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class Memory(Base):
    """Explicit instance-level preferences only; no inferred conversation memory."""
    __tablename__ = "memory"
    __table_args__ = (
        UniqueConstraint("key", name="uq_memory_key"),
        CheckConstraint("key IN ('answer_style', 'learning_goal', 'preferred_language')", name="ck_memory_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
