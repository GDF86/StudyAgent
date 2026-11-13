from contextlib import aclosing
from dataclasses import dataclass
import logging

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import DomainError
from app.core.live_test import forbid_extra
from app.llm.provider import LLMProvider
from app.rag.prompts import build_messages, validate_citation_markers
from app.rag.retriever import Retriever
from app.schemas.chat import ChatRequest, ChatResponse, Citation

log = logging.getLogger("uvicorn.error")


@dataclass
class PreparedChat:
    knowledge_base_id: int
    citations: list[Citation]
    messages: list[dict[str, str]]
    immediate: ChatResponse | None = None


class ChatService:
    """One independent RAG question; no history, memory, tools, or agents."""

    def __init__(self, settings: Settings, retriever: Retriever, provider: LLMProvider,preferences=None):
        self.settings, self.retriever, self.provider = settings, retriever, provider
        self.preferences=preferences

    def prepare(self, session: Session, body: ChatRequest) -> PreparedChat:
        result = self.retriever.retrieve(session, body.knowledge_base_id, body.question, body.top_k or self.settings.top_k)
        prepared = PreparedChat(body.knowledge_base_id, result.citations, [])
        if not result.citations:
            status = "no_relevant_content" if result.has_ready_documents else "empty_knowledge_base"
            answer = "未检索到足够相关的资料，无法依据当前知识库回答。" if result.has_ready_documents else "当前知识库没有已就绪的文档，请先上传并成功处理资料。"
            prepared.immediate = ChatResponse(knowledge_base_id=body.knowledge_base_id, answer=answer, citations=[], status=status)
        else:
            prepared.messages = build_messages(body.question, result.citations)
            if self.preferences:
                from app.services.preferences import inject_preferences
                inject_preferences(prepared.messages,self.preferences.get().preferences)
        return prepared

    def finish(self, prepared: PreparedChat, answer: str) -> ChatResponse:
        grounded = validate_citation_markers(answer, prepared.citations)
        return ChatResponse(knowledge_base_id=prepared.knowledge_base_id, answer=answer,
                            citations=prepared.citations if grounded else [],
                            status="answered" if grounded else "insufficient_evidence")

    async def answer(self, prepared: PreparedChat) -> ChatResponse:
        if prepared.immediate:
            return prepared.immediate
        return self.finish(prepared, await self.provider.complete(prepared.messages))

    async def events(self, prepared: PreparedChat):
        """Send provisional tokens; only done confirms a validated final answer."""
        try:
            for citation in prepared.citations:
                yield {"type": "citation", "data": citation.model_dump()}
            if prepared.immediate:
                final = prepared.immediate
                yield {"type": "token", "content": final.answer}
            else:
                parts = []
                async with aclosing(self.provider.stream(prepared.messages)) as tokens:
                    async for token in tokens:
                        parts.append(token)
                        yield {"type": "token", "content": token}
                final = self.finish(prepared, "".join(parts))
            yield {"type": "done", **final.model_dump()}
        except DomainError as error:
            if error.code!='LLM_OUTPUT_LIMIT':forbid_extra("CHAT_FAILED")
            log.warning("chat_failed code=%s", error.code)
            yield {"type": "error", "code": error.code, "message": error.message}
        except Exception:
            forbid_extra("CHAT_FAILED")
            log.warning("chat_failed code=INTERNAL_ERROR")
            yield {"type": "error", "code": "INTERNAL_ERROR", "message": "问答暂时不可用，请重试"}
