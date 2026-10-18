from dataclasses import dataclass
import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import DomainError
from app.database.models import Chunk, Document, KnowledgeBase
from app.schemas.chat import Citation
from app.services.documents import DocumentService
from app.rag.reranker import Reranker

log = logging.getLogger("uvicorn.error")


@dataclass
class RetrievalResult:
    citations: list[Citation]
    has_ready_documents: bool


class Retriever:
    """Search only ready documents in the selected KB and globally merge Top-K."""

    def __init__(self, settings: Settings, documents: DocumentService):
        self.settings, self.documents = settings, documents
        self.reranker = Reranker(settings)

    def retrieve(self, session: Session, kb_id: int, question: str, top_k: int, *, rerank: bool | None = None) -> RetrievalResult:
        enabled = self.settings.enable_reranker if rerank is None else rerank
        candidate_k = max(top_k, self.settings.reranker_candidate_k) if enabled else top_k
        # Ingestion/deletion uses this same lock. Snapshot citations before releasing it.
        with self.documents.lock:
            if session.get(KnowledgeBase, kb_id) is None:
                raise DomainError(404, "NOT_FOUND", "知识库不存在")
            docs = session.scalars(select(Document).where(Document.knowledge_base_id == kb_id, Document.status == "ready").order_by(Document.id)).all()
            if not docs:
                return RetrievalResult([], False)
            if any(doc.embedding_model != self.settings.embedding_model for doc in docs):
                raise DomainError(409, "EMBEDDING_MODEL_MISMATCH", "当前 Embedding 模型与知识库索引不一致，请恢复原模型或重新上传资料")
            vectors = self.documents.embeddings.embed_texts([question])
            if len(vectors) != 1:
                raise DomainError(502, "EMBEDDING_INVALID_RESPONSE", "问题向量响应无效")
            query = np.asarray(vectors[0], dtype="float64")
            if query.ndim != 1 or not query.size or not np.isfinite(query).all() or np.linalg.norm(query) == 0:
                raise DomainError(502, "EMBEDDING_INVALID_RESPONSE", "问题向量响应无效")
            query = (query / np.linalg.norm(query)).astype("float32").reshape(1, -1)
            candidates: list[Citation] = []
            for doc in docs:
                if doc.embedding_dimension != query.shape[1]:
                    raise DomainError(409, "EMBEDDING_DIMENSION_MISMATCH", "问题向量与文档向量维度不同，请检查 Embedding 配置")
                try:
                    index = self.documents.vectors.read(doc.id)
                    if index.d != query.shape[1] or index.ntotal != doc.chunk_count or not index.ntotal:
                        raise ValueError("inconsistent index")
                    scores, ordinals = index.search(query, min(candidate_k, index.ntotal))
                except Exception:
                    raise DomainError(409, "VECTOR_INDEX_UNAVAILABLE", "文档向量索引缺失或损坏，请重新上传该文档") from None
                chunks = {chunk.ordinal: chunk for chunk in session.scalars(select(Chunk).where(Chunk.document_id == doc.id, Chunk.ordinal.in_([int(i) for i in ordinals[0]]))).all()}
                for score, ordinal in zip(scores[0], ordinals[0]):
                    chunk = chunks.get(int(ordinal))
                    if chunk is None or chunk.vector_id != f"{doc.id}:{int(ordinal)}":
                        raise DomainError(409, "VECTOR_INDEX_UNAVAILABLE", "文档分片与向量索引不一致，请重新上传该文档")
                    if not np.isfinite(score):
                        raise DomainError(409, "VECTOR_INDEX_UNAVAILABLE", "文档向量索引包含无效数值，请重新上传")
                    if float(score) < self.settings.retrieval_min_score:
                        continue
                    candidates.append(Citation(id=0, knowledge_base_id=kb_id, document_id=doc.id,
                                               document_name=doc.filename, page=chunk.page, chunk_id=chunk.id,
                                               content=chunk.content, score=max(-1.0, min(1.0, float(score)))))
            selected = sorted(candidates, key=lambda item: (-item.score, item.document_id, item.chunk_id))[:candidate_k]
            if enabled:
                selected = self.reranker.rank(question, selected, top_k)
            if sum(len(item.content) for item in selected) > self.settings.rag_max_context_chars:
                raise DomainError(413, "RAG_CONTEXT_TOO_LARGE", "检索原文超过上下文限制，请调小 Top-K 或分片大小")
            for number, citation in enumerate(selected, 1):
                citation.id = number
            log.info("retrieval_completed kb_id=%s chunks=%s", kb_id, len(selected))
            return RetrievalResult(selected, True)
