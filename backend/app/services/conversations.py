"""Short, explicit transactions for history; no model calls or context assembly."""
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.database.models import Chunk, Conversation, Document, KnowledgeBase, Message, utc_now
from app.schemas.conversation import (ConversationCreate, ConversationPage, ConversationQuery,
    ConversationRead, ConversationUpdate, MessagePage, MessageQuery, MessageRead, MessageWrite)


class ConversationService:
    def __init__(self, engine, lock):
        self.engine, self.lock = engine, lock
        self.active: dict[str, str] = {}

    @contextmanager
    def transaction(self):
        """Session.begin rolls back on every exception; the shared lock serializes KB deletion."""
        with self.lock:
            try:
                with Session(self.engine) as session, session.begin():
                    yield session
            except IntegrityError:
                raise DomainError(409, "HISTORY_CONFLICT", "会话或消息记录冲突，请刷新后重试") from None
            except SQLAlchemyError:
                raise DomainError(500, "HISTORY_STORAGE_ERROR", "历史记录保存或读取失败，请重试") from None

    @staticmethod
    def require_kb(session, kb_id):
        if session.get(KnowledgeBase, kb_id) is None:
            raise DomainError(404, "NOT_FOUND", "知识库不存在")

    @classmethod
    def require_conversation(cls, session, conversation_id, kb_id=None):
        item = session.get(Conversation, str(conversation_id))
        if item is None or (kb_id is not None and item.knowledge_base_id != kb_id):
            raise DomainError(404, "CONVERSATION_NOT_FOUND", "当前范围内的会话不存在")
        cls.require_kb(session, item.knowledge_base_id)
        return item

    def create(self, body: ConversationCreate) -> ConversationRead:
        with self.transaction() as session:
            self.require_kb(session, body.knowledge_base_id)
            item = Conversation(**body.model_dump())
            session.add(item)
            session.flush()
            result = ConversationRead.model_validate(item)
        return result

    def list_conversations(self, query: ConversationQuery) -> ConversationPage:
        with self.transaction() as session:
            conditions = []
            if query.knowledge_base_id is not None:
                self.require_kb(session, query.knowledge_base_id)
                conditions.append(Conversation.knowledge_base_id == query.knowledge_base_id)
            if query.mode is not None:
                conditions.append(Conversation.mode == query.mode)
            total = session.scalar(select(func.count()).select_from(Conversation).where(*conditions))
            rows = session.scalars(select(Conversation).where(*conditions)
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc()).offset(query.offset).limit(query.limit)).all()
            return ConversationPage(items=[ConversationRead.model_validate(row) for row in rows], total=total,
                                    limit=query.limit, offset=query.offset)

    def get(self, conversation_id: UUID, kb_id=None) -> ConversationRead:
        with self.transaction() as session:
            return ConversationRead.model_validate(self.require_conversation(session, conversation_id, kb_id))

    def require_idle(self, conversation_id):
        if str(conversation_id) in self.active:
            raise DomainError(409, "CONVERSATION_BUSY", "会话正在生成，请先停止任务")

    def require_knowledge_base_idle(self, session, kb_id):
        ids = session.scalars(select(Conversation.id).where(Conversation.knowledge_base_id == kb_id))
        for conversation_id in ids:
            self.require_idle(conversation_id)

    def rename(self, conversation_id: UUID, body: ConversationUpdate, kb_id=None) -> ConversationRead:
        with self.transaction() as session:
            item = self.require_conversation(session, conversation_id, kb_id)
            self.require_idle(item.id)
            item.title = body.title
            item.updated_at = utc_now()
            item.revision += 1
            session.flush()
            result = ConversationRead.model_validate(item)
        return result

    def delete(self, conversation_id: UUID, kb_id=None) -> None:
        with self.transaction() as session:
            item = self.require_conversation(session, conversation_id, kb_id)
            self.require_idle(item.id)
            session.execute(delete(Message).where(Message.conversation_id == item.id))
            session.delete(item)

    def delete_for_knowledge_base(self, session: Session, kb_id: int) -> None:
        """Participate in the caller's KB-delete transaction; never commit independently."""
        self.require_knowledge_base_idle(session, kb_id)
        ids = select(Conversation.id).where(Conversation.knowledge_base_id == kb_id)
        session.execute(delete(Message).where(Message.conversation_id.in_(ids)))
        session.execute(delete(Conversation).where(Conversation.knowledge_base_id == kb_id))

    def messages(self, conversation_id: UUID, query: MessageQuery) -> MessagePage:
        with self.transaction() as session:
            item = self.require_conversation(session, conversation_id, query.knowledge_base_id)
            condition = Message.conversation_id == item.id
            total = session.scalar(select(func.count()).select_from(Message).where(condition))
            rows = session.scalars(select(Message).where(condition).order_by(Message.turn_no, Message.attempt_no,
                case((Message.role == "user", 0), else_=1), Message.id).offset(query.offset).limit(query.limit)).all()
            return MessagePage(items=[MessageRead.model_validate(row) for row in rows], total=total,
                               limit=query.limit, offset=query.offset)

    @staticmethod
    def validate_sources(session, conversation, body):
        metadata = body.result_metadata
        if ((conversation.mode == "chat" and metadata.tool_calls is not None)
                or (conversation.mode == "agent" and metadata.chat_status is not None)):
            raise DomainError(422, "INVALID_MESSAGE_METADATA", "消息元数据与会话模式不一致")
        for source in body.citations:
            doc = session.get(Document, str(source.document_id))
            chunk = session.get(Chunk, str(source.chunk_id))
            if (source.knowledge_base_id != conversation.knowledge_base_id or doc is None or chunk is None
                    or doc.knowledge_base_id != conversation.knowledge_base_id or chunk.document_id != doc.id
                    or source.document_name != doc.filename or source.page != chunk.page or source.content != chunk.content):
                raise DomainError(422, "INVALID_CITATION_SNAPSHOT", "引用快照与当前知识库原文不一致")

    def save_messages(self, conversation_id: UUID, knowledge_base_id: int, messages: list[MessageWrite]) -> list[MessageRead]:
        """Atomically insert a bounded batch; caller supplies state, no generation occurs here."""
        if not messages or len(messages) > 100:
            raise DomainError(422, "INVALID_MESSAGE_BATCH", "每批需包含 1 至 100 条消息")
        with self.transaction() as session:
            conversation = self.require_conversation(session, conversation_id, knowledge_base_id)
            self.require_idle(conversation.id)
            rows = []
            for body in messages:
                self.validate_sources(session, conversation, body)
                data = body.model_dump(mode="json")
                data["result_metadata"] = body.result_metadata.model_dump(exclude_none=True)
                row = Message(conversation_id=conversation.id, **data)
                session.add(row)
                rows.append(row)
            conversation.updated_at = utc_now()
            conversation.revision += 1
            session.flush()
            result = [MessageRead.model_validate(row) for row in rows]
        return result

    def save_message(self, conversation_id: UUID, knowledge_base_id: int, body: MessageWrite) -> MessageRead:
        return self.save_messages(conversation_id, knowledge_base_id, [body])[0]
