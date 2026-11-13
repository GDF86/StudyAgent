from typing import Annotated
from uuid import UUID
from tempfile import TemporaryDirectory
from pathlib import Path

from fastapi import APIRouter, File, Request, Response, UploadFile
from sqlalchemy import select

from app.api.knowledge_bases import Database, require_knowledge_base,protect_reference
from app.core.errors import DomainError
from app.database.models import Chunk, Document
from app.schemas.document import ChunkRead, DocumentDetail, DocumentRead

router = APIRouter(prefix="/api", tags=["Documents"])

@router.post('/knowledge-bases/{kb_id}/documents/preview')
def preview_upload(kb_id:int,request:Request,session:Database,file:Annotated[UploadFile,File()]):
    try:
        protect_reference(require_knowledge_base(session,kb_id),request)
        suffix=Path(file.filename or '').suffix.lower()
        if suffix not in {'.pdf','.docx','.txt','.md'}:raise DomainError(400,'UNSUPPORTED_FILE_TYPE','仅支持 PDF、DOCX、TXT 和 MD 文件')
        data=file.file.read(request.app.state.settings.max_upload_bytes+1)
        if len(data)>request.app.state.settings.max_upload_bytes:raise DomainError(413,'FILE_TOO_LARGE','文件超过上传大小限制；Embedding未调用。')
        if not data:raise DomainError(400,'EMPTY_FILE','文件为空；Embedding未调用。')
        with TemporaryDirectory(prefix='studyagent-preview-') as folder:
            path=Path(folder)/('preview'+suffix);path.write_bytes(data)
            return request.app.state.documents.processing_plan(path,suffix)
    finally:file.file.close()

@router.get('/documents/{document_id}/processing-plan')
def processing_plan(document_id:UUID,request:Request,session:Database):
    doc=require_document(session,document_id)
    path=request.app.state.documents.raw_path(doc)
    if not path.exists():raise DomainError(409,'SOURCE_MISSING','原始文件不存在，请重新选择原文件；Embedding未调用。')
    return request.app.state.documents.processing_plan(path,doc.file_type)

@router.post('/documents/{document_id}/retry',response_model=DocumentRead)
def retry_document(document_id:UUID,request:Request,session:Database):
    service=request.app.state.documents
    with service.lock:
        doc=require_document(session,document_id)
        protect_reference(require_knowledge_base(session,doc.knowledge_base_id),request)
        if doc.status!='failed':raise DomainError(409,'DOCUMENT_NOT_FAILED','只有失败文档可以重试，不会重复处理已就绪资料')
        if not service.raw_path(doc).exists():raise DomainError(409,'SOURCE_MISSING','原始文件不存在，请重新选择原文件')
        if request.headers.get('X-Confirm-Retry')!='true':raise DomainError(403,'RETRY_CONFIRMATION_REQUIRED','请先分析工作量并确认重新生成索引，可能产生模型费用')
        service.processing_plan(service.raw_path(doc),doc.file_type)
        doc.status='processing';doc.error_message=None;session.commit()
        return service.process_document(session,doc)


def require_document(session, document_id: UUID) -> Document:
    doc = session.get(Document, str(document_id))
    if doc is None:
        raise DomainError(404, "NOT_FOUND", "文档不存在")
    return doc


@router.post("/knowledge-bases/{kb_id}/documents", response_model=DocumentRead, status_code=201)
def upload_document(kb_id: int, request: Request, session: Database, file: Annotated[UploadFile, File()]):
    try:
        protect_reference(require_knowledge_base(session,kb_id),request)
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
        doc=require_document(session,document_id)
        protect_reference(require_knowledge_base(session,doc.knowledge_base_id),request)
        request.app.state.documents.delete_document(session,doc)
    return Response(status_code=204)
