from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import KnowledgeBase, Document
from app.database.session import get_session
from app.core.errors import DomainError
from app.schemas.knowledge_base import KnowledgeBaseRead, KnowledgeBaseWrite

router = APIRouter(prefix="/api/knowledge-bases", tags=["Knowledge bases"])
Database = Annotated[Session, Depends(get_session)]

def protect_reference(item,request):
    if request.app.state.settings.web_demo_enabled and item.name=='Bailian-RAG-Live-Test':
        raise DomainError(403,'REFERENCE_KB_PROTECTED','历史验证知识库只读，请创建自己的学习知识库')


def require_knowledge_base(session: Session, kb_id: int) -> KnowledgeBase:
    """Fetch a knowledge base or return the common 404 error."""
    item = session.get(KnowledgeBase, kb_id)
    if item is None:
        raise HTTPException(404, "知识库不存在")
    return item


def save(session: Session, item: KnowledgeBase) -> KnowledgeBase:
    """Persist changes; uniqueness is enforced in the database, including races."""
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "知识库名称已存在") from None
    session.refresh(item)
    return item


@router.get("", response_model=list[KnowledgeBaseRead])
def list_knowledge_bases(session: Database):
    return session.scalars(select(KnowledgeBase).order_by(KnowledgeBase.updated_at.desc(), KnowledgeBase.id.desc())).all()


@router.post("", response_model=KnowledgeBaseRead, status_code=201)
def create_knowledge_base(body: KnowledgeBaseWrite, session: Database):
    item = KnowledgeBase(**body.model_dump())
    session.add(item)
    return save(session, item)


@router.get("/{kb_id}", response_model=KnowledgeBaseRead)
def get_knowledge_base(kb_id: int, session: Database):
    return require_knowledge_base(session, kb_id)


@router.put("/{kb_id}", response_model=KnowledgeBaseRead)
def update_knowledge_base(kb_id: int, body: KnowledgeBaseWrite, session: Database,request:Request):
    item = require_knowledge_base(session, kb_id)
    protect_reference(item,request)
    item.name, item.description = body.name, body.description
    return save(session, item)


@router.delete("/{kb_id}", status_code=204)
def delete_knowledge_base(kb_id: int, session: Database, request: Request):
    service = request.app.state.documents
    with service.lock:
        item = require_knowledge_base(session, kb_id)
        protect_reference(item,request)
        request.app.state.conversations.require_knowledge_base_idle(session, kb_id)
        for doc in session.scalars(select(Document).where(Document.knowledge_base_id == kb_id)).all():
            service.delete_document(session, doc)
        request.app.state.conversations.delete_for_knowledge_base(session, kb_id)
        session.delete(item)
        session.commit()
    return Response(status_code=204)
