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
                raise DomainError(409, "DOCUMENT_DUPLICATE", "该文件已上传；失败记录请先删除后重新上传")
            doc = Document(id=str(uuid4()), knowledge_base_id=kb_id, filename=name, file_type=suffix,
                           file_size=len(data), content_hash=digest, status="processing")
            session.add(doc)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise DomainError(409, "DOCUMENT_DUPLICATE", "该文件已上传") from None
            try:
                self.raw_path(doc).write_bytes(data)
                log.info("document_uploaded document_id=%s bytes=%s", doc.id, doc.file_size)
                pages = parse_document(self.raw_path(doc), suffix, self.settings.max_text_chars)
                log.info("document_parsed document_id=%s", doc.id)
                pieces = split_pages(pages, self.settings.chunk_size, self.settings.chunk_overlap, self.settings.max_chunks)
                doc.status = "embedding"
                session.commit()
                log.info("embedding_started document_id=%s chunks=%s", doc.id, len(pieces))
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
                doc.status, doc.error_message, doc.chunk_count = "failed", public.message, 0
                session.commit()
                log.warning("document_failed document_id=%s code=%s", doc_id, public.code)
                public.detail = {"document_id": doc_id}
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
