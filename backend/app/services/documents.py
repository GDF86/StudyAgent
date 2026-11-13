from hashlib import sha256
import logging
from pathlib import Path
from threading import RLock
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import DomainError
from app.database.models import Chunk, Document, KnowledgeBase
from app.rag.embeddings import EmbeddingProvider
from app.rag.loaders import parse_document
from app.rag.splitter import split_pages
from app.rag.vector_store import VectorStore

log = logging.getLogger("uvicorn.error")
SUPPORTED = {".pdf", ".docx", ".txt", ".md"}


class DocumentService:
    """Coordinate file/SQL/FAISS lifecycle in one local process, including recovery."""

    def __init__(self, settings: Settings, embeddings: EmbeddingProvider):
        self.settings, self.embeddings = settings, embeddings
        self.upload_dir = settings.data_dir / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.vectors = VectorStore(settings.data_dir / "vectors")
        self.lock = RLock()

    def raw_path(self, doc: Document) -> Path:
        # Neither the display filename nor any client-provided path is used on disk.
        return self.upload_dir / f"{doc.id}{doc.file_type}"

    def ingest(self, session: Session, kb_id: int, file: UploadFile) -> Document:
        """Persist the upload, parse/chunk/embed it, then publish a ready document."""
        name = (file.filename or "").strip()
        if not name or len(name) > 255 or any(c in name for c in ("/", "\\", "\x00")) or any(ord(c) < 32 for c in name):
            raise DomainError(400, "INVALID_FILENAME", "文件名无效，不允许路径分隔符或控制字符")
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED:
            raise DomainError(400, "UNSUPPORTED_FILE_TYPE", "仅支持 PDF、DOCX、TXT 和 MD 文件")
        data = file.file.read(self.settings.max_upload_bytes + 1)
        if len(data) > self.settings.max_upload_bytes:
            raise DomainError(413, "FILE_TOO_LARGE", "上传文件超过大小限制")
        if not data:
            raise DomainError(400, "EMPTY_FILE", "不能上传空文件")
        digest = sha256(data).hexdigest()
        with self.lock:
            if session.get(KnowledgeBase, kb_id) is None:
                raise DomainError(404, "NOT_FOUND", "知识库不存在")
            if session.scalar(select(Document.id).where(Document.knowledge_base_id == kb_id, Document.content_hash == digest)):
                raise DomainError(409, "DOCUMENT_DUPLICATE", "该文件已上传；失败记录可先分析原文件，再确认重试")
            doc = Document(id=str(uuid4()), knowledge_base_id=kb_id, filename=name, file_type=suffix,
                           file_size=len(data), content_hash=digest, status="processing")
            session.add(doc)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise DomainError(409, "DOCUMENT_DUPLICATE", "该文件已上传") from None
            return self.process_document(session,doc,data)

    def split(self,pages):
        return split_pages(pages,self.settings.chunk_size,self.settings.chunk_overlap,self.settings.max_chunks,
            paragraph_aware=self.settings.paragraph_aware_chunks,max_utf8_bytes=self.settings.chunk_max_utf8_bytes)

    def processing_plan(self,path,suffix):
        """Local-only analysis. No embeddings, index writes or ledger mutations."""
        pages=parse_document(path,suffix,self.settings.max_text_chars)
        pieces=self.split(pages)
        batch=self.settings.embedding_batch_size
        calls=(len(pieces)+batch-1)//batch
        bounds=[len(p.content.encode('utf-8'))*4+256 for p in pieces]
        if self.settings.web_demo_enabled and (len(pieces)>128 or max(bounds)>8192 or calls>16):
            raise DomainError(413,'UPLOAD_MODEL_LIMIT',f'解析{sum(len(p.content) for p in pages)}字符，计划{len(pieces)}片/{calls}批，超出本机模型单片或总批次限制；请按章节分卷。Embedding未调用。')
        cost=None
        if self.settings.web_demo_enabled:
            import sqlite3,time
            try:
                with sqlite3.connect(self.settings.live_test_ledger.resolve().as_uri()+'?mode=ro',uri=True) as c:
                    row=c.execute("SELECT r.embedding_rate FROM run r JOIN development_sessions d ON d.run_id=r.id WHERE r.id=? AND r.state='READY' AND r.authorized=1 AND r.verified_until>? AND d.expires>?",(self.settings.web_demo_round_id,time.time(),time.time())).fetchone()
                    if row and row[0]:cost=sum(bounds)*row[0]/1e9
                    if getattr(self.settings,'personal_learning',False):
                        from app.core.personal_learning import is_personal,fingerprint
                        if is_personal(c,self.settings.web_demo_round_id):
                            profile=c.execute('SELECT p.binding,p.embedding_rate FROM personal_prices p JOIN personal_runs r ON r.price_id=p.id WHERE r.run_id=?',(self.settings.web_demo_round_id,)).fetchone()
                            try:cost=sum(bounds)*profile[1]/1e9 if profile[0]==fingerprint(self.settings) else None
                            except DomainError:cost=None
            except (sqlite3.Error,OSError):pass  # Local parsing remains available; price stays unknown.
        return {'parsed_chars':sum(len(p.content) for p in pages),'planned_chunks':len(pieces),'embedding_requests':calls,'max_attempts_with_retry':calls*2,
                'estimated_reserve_yuan':cost,'worst_reserve_with_retry_yuan':cost*2 if cost is not None else None,'embedding_called':False}

    def process_document(self,session,doc,data=None):
        """Explicit ingest/retry only. Retain original raw file on every failure."""
        with self.lock:
            embedding_entered=False
            parsed_chars=0
            planned_chunks=0
            try:
                if data is not None:self.raw_path(doc).write_bytes(data)
                log.info("document_uploaded document_id=%s bytes=%s", doc.id, doc.file_size)
                pages = parse_document(self.raw_path(doc), doc.file_type, self.settings.max_text_chars)
                log.info("document_parsed document_id=%s", doc.id)
                parsed_chars=sum(len(p.content) for p in pages)
                pieces = self.split(pages)
                planned_chunks=len(pieces)
                from app.core.development_budget import preflight_document
                preflight_document(self.settings,[piece.content for piece in pieces])
                doc.status = "embedding"
                session.commit()
                log.info("embedding_started document_id=%s chunks=%s", doc.id, len(pieces))
                embedding_entered=True
                vectors = self.embeddings.embed_texts([piece.content for piece in pieces])
                if len(vectors) != len(pieces):
                    raise DomainError(502, "EMBEDDING_INVALID_RESPONSE", "Embedding 向量数量与文本分片不一致")
                self.vectors.write(doc.id, vectors)
                for ordinal, piece in enumerate(pieces):
                    session.add(Chunk(id=str(uuid4()), document_id=doc.id, ordinal=ordinal, page=piece.page,
                                      content=piece.content, vector_id=f"{doc.id}:{ordinal}"))
                doc.status, doc.chunk_count = "ready", len(pieces)
                doc.embedding_model = self.settings.embedding_model
                doc.embedding_dimension = len(vectors[0])
                session.commit()
                log.info("embedding_completed document_id=%s vectors=%s", doc.id, doc.chunk_count)
                return doc
            except Exception as error:
                doc_id = doc.id
                session.rollback()
                self.vectors.delete(doc_id)
                session.execute(delete(Chunk).where(Chunk.document_id == doc_id))
                doc = session.get(Document, doc_id)
                public = error if isinstance(error, DomainError) else DomainError(500, "DOCUMENT_PROCESSING_FAILED", "文档处理失败，请稍后重新上传")
                if isinstance(public.detail,dict):planned_chunks=public.detail.get('planned_chunks',planned_chunks)
                phase="已进入向量化，实际请求及费用以账本为准。" if embedding_entered else "失败发生在向量化前，未调用Embedding。"
                public.message += f"（解析{parsed_chars}字符，计划{planned_chunks}片；{phase}）"
                doc.status, doc.error_message, doc.chunk_count = "failed", public.message, 0
                session.commit()
                log.warning("document_failed document_id=%s code=%s", doc_id, public.code)
                public.detail = {"document_id": doc_id,'parsed_chars':parsed_chars,'planned_chunks':planned_chunks,'embedding_phase_entered':embedding_entered}
                raise public from None

    def delete_document(self, session: Session, doc: Document) -> None:
        """Mark deletion first so interruptions can safely resume at startup."""
        with self.lock:
            doc.status = "deleting"
            session.commit()
            self.vectors.delete(doc.id)
            self.raw_path(doc).unlink(missing_ok=True)
            try:
                session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
                session.delete(doc)
                session.commit()
            except Exception:
                # Keep the committed deleting marker visible after a failed COMMIT.
                # A request-scoped close alone may return an unrolled-back connection.
                session.rollback()
                raise
            log.info("document_deleted")

    def recover(self, session: Session) -> None:
        """Recover interrupted ingest/delete operations before accepting requests."""
        for doc in session.scalars(select(Document)).all():
            if doc.status == "deleting":
                self.delete_document(session, doc)
            elif doc.status in {"processing", "embedding"}:
                self.vectors.delete(doc.id)
                session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
                doc.status, doc.chunk_count = "failed", 0
                doc.error_message = "处理被服务重启中断，请删除后重新上传"
        session.commit()
