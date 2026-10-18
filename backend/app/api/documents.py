from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Request, Response, UploadFile
from sqlalchemy import select

from app.api.knowledge_bases import Database, require_knowledge_base
from app.core.errors import DomainError
from app.database.models import Chunk, Document
from app.schemas.document import ChunkRead, DocumentDetail, DocumentRead

router = APIRouter(prefix="/api", tags=["Documents"])


def require_document(session, document_id: UUID) -> Document:
    doc = session.get(Document, str(document_id))
    if doc is None:
        raise DomainError(404, "NOT_FOUND", "文档不存在")
    return doc


@router.post("/knowledge-bases/{kb_id}/documents", response_model=DocumentRead, status_code=201)
def upload_document(kb_id: int, request: Request, session: Database, file: Annotated[UploadFile, File()]):
    try:
        return request.app.state.documents.ingest(session, kb_id, file)
    finally:
        file.file.close()


@router.get("/knowledge-bases/{kb_id}/documents", response_model=list[DocumentRead])
def list_documents(kb_id: int, session: Database):
    require_knowledge_base(session, kb_id)
    return session.scalars(select(Document).where(Document.knowledge_base_id == kb_id).order_by(Document.created_at.desc())).all()


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(document_id: UUID, session: Database):
    doc = require_document(session, document_id)
    chunks = session.scalars(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.ordinal)).all()
    return DocumentDetail(**DocumentRead.model_validate(doc).model_dump(), source=doc.filename,
                          chunks=[ChunkRead.model_validate(chunk) for chunk in chunks])


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: UUID, request: Request, session: Database):
    with request.app.state.documents.lock:
        request.app.state.documents.delete_document(session, require_document(session, document_id))
    return Response(status_code=204)
