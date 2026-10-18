"""Real tool execution. Knowledge-base scope is server-owned, never a tool argument."""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from app.agent.calculator import calculate
from app.core.errors import DomainError
from app.database.models import Chunk, Document, KnowledgeBase
from app.rag.prompts import build_messages, validate_citation_markers
from app.schemas.chat import Citation


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Search(Arguments):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)


class Summarize(Arguments):
    document_id: str = Field(min_length=1, max_length=36)


class Compare(Arguments):
    document_ids: list[str] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.document_ids)) != len(self.document_ids) or any(not x or len(x) > 36 for x in self.document_ids):
            raise ValueError("distinct document IDs required")
        return self


class Quiz(Arguments):
    topic: str = Field(min_length=1, max_length=1000)
    count: int = Field(default=3, ge=1, le=6)
    question_type: Literal["multiple_choice", "short_answer", "true_false"] = "short_answer"


class Calculator(Arguments):
    expression: str = Field(min_length=1, max_length=256)


REGISTRY = {
    "search_documents": (Search, "检索当前知识库的相关原文和引用。"),
    "summarize_document": (Summarize, "依据当前知识库指定文档的全部分片生成有引用的总结；过长文档明确拒绝。"),
    "compare_documents": (Compare, "依据当前知识库的 2 至 4 篇不同文档生成有引用的比较。"),
    "generate_quiz": (Quiz, "检索当前知识库并生成附答案、解释和引用的选择题、简答题或判断题。"),
    "calculator": (Calculator, "计算基础数学表达式，支持 + - * / % ** 和括号。"),
}


def definitions():
    return [{"type": "function", "function": {"name": name, "description": description,
            "parameters": schema.model_json_schema()}} for name, (schema, description) in REGISTRY.items()]


class Question(Arguments):
    question: str = Field(min_length=1, max_length=2000)
    options: list[str] = Field(default_factory=list, max_length=4)
    answer: str = Field(min_length=1, max_length=2000)
    explanation: str = Field(min_length=1, max_length=4000)
    citation_ids: list[int] = Field(min_length=1, max_length=20)


class QuizResult(Arguments):
    questions: list[Question] = Field(min_length=1, max_length=6)


class ToolContext:
    def __init__(self, settings, engine, documents, retriever, provider, kb_id):
        self.settings, self.engine, self.documents = settings, engine, documents
        self.retriever, self.provider, self.kb_id = retriever, provider, kb_id
        self.citations: list[Citation] = []

    def inventory(self):
        with self.documents.lock, Session(self.engine) as session:
            if session.get(KnowledgeBase, self.kb_id) is None:
                raise DomainError(404, "NOT_FOUND", "知识库不存在")
            docs = session.scalars(select(Document).where(Document.knowledge_base_id == self.kb_id,
                Document.status == "ready").order_by(Document.id).limit(101)).all()
            return {"documents": [{"document_id": d.id, "name": d.filename} for d in docs[:100]],
                    "inventory_truncated": len(docs) > 100}

    def sources(self, ids):
        with self.documents.lock, Session(self.engine) as session:
            result = []
            for doc_id in ids:
                doc = session.scalar(select(Document).where(Document.id == doc_id,
                    Document.knowledge_base_id == self.kb_id, Document.status == "ready"))
                if doc is None:
                    raise DomainError(404, "DOCUMENT_UNAVAILABLE", "指定文档不在当前知识库或尚未就绪")
                chunks = session.scalars(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.ordinal)).all()
                if not chunks:
                    raise DomainError(409, "DOCUMENT_UNAVAILABLE", "文档没有可用分片")
                result.extend(Citation(id=0, knowledge_base_id=self.kb_id, document_id=doc.id,
                    document_name=doc.filename, page=c.page, chunk_id=c.id, content=c.content, score=1.0) for c in chunks)
            self.check_size(result)
            return result

    def search(self, query, top_k):
        with Session(self.engine) as session:
            return self.retriever.retrieve(session, self.kb_id, query, top_k).citations

    def check_size(self, sources):
        if sum(len(c.content) for c in sources) > self.settings.rag_max_context_chars:
            raise DomainError(413, "AGENT_CONTEXT_TOO_LARGE", "文档内容超过本次工具上下文限制，请拆分文档或缩小任务范围")

    def register(self, sources):
        existing = {c.chunk_id: c for c in self.citations}
        result = []
        for source in sources:
            if source.chunk_id not in existing:
                source = source.model_copy(update={"id": len(self.citations) + 1})
                self.citations.append(source)
                existing[source.chunk_id] = source
            result.append(existing[source.chunk_id])
        self.check_size(self.citations)
        return result

    async def execute(self, name, raw):
        if name not in REGISTRY:
            raise DomainError(422, "UNKNOWN_TOOL", "请求的工具不存在")
        try:
            args = REGISTRY[name][0].model_validate_json(raw)
        except (ValidationError, ValueError):
            raise DomainError(422, "INVALID_TOOL_ARGUMENTS", "工具参数不符合定义，请按工具 JSON Schema 修正") from None
        if name == "calculator":
            return {"expression": args.expression, "value": calculate(args.expression)}
        if name in {"search_documents", "generate_quiz"}:
            sources = await run_in_threadpool(self.search, args.query if name == "search_documents" else args.topic,
                                             args.top_k if name == "search_documents" else self.settings.top_k)
        else:
            sources = await run_in_threadpool(self.sources, [args.document_id] if name == "summarize_document" else args.document_ids)
        sources = self.register(sources)
        if not sources:
            return {"status": "no_relevant_content", "citations": [], "message": "当前知识库没有足够相关资料"}
        material = [c.model_dump() for c in sources]
        if name == "search_documents":
            return {"citations": material}
        if name == "generate_quiz":
            instruction = (f"生成 {args.count} 道 {args.question_type} 题，主题：{args.topic}。只输出 JSON，"
                '格式 {"questions":[{"question":"题干","options":[],"answer":"答案","explanation":"解释",'
                '"citation_ids":[1]}]}。multiple_choice 必须有四个不同选项，answer 等于正确选项全文；'
                'true_false 的 answer 只能为 true 或 false 字符串；其他题 options 为空。citation_ids 使用本次资料编号。')
        else:
            instruction = "总结这篇文档的主要内容、结论和局限。" if name == "summarize_document" else "比较这些文档的共同点、差异和各自依据，明确区分文档。"
        messages = build_messages(instruction, sources)
        if name == "generate_quiz":
            messages[0]["content"] += "本次为结构化出题任务，以 citation_ids 代替行内引用，只输出要求的 JSON。"
        answer = await self.provider.complete(messages)
        if name != "generate_quiz":
            validate_citation_markers(answer, sources)
            return {"text": answer, "citations": material}
        try:
            quiz = QuizResult.model_validate_json(answer)
            if len(quiz.questions) != args.count:
                raise ValueError()
            allowed = {c.id for c in sources}
            for q in quiz.questions:
                if not set(q.citation_ids).issubset(allowed):
                    raise ValueError()
                if args.question_type == "multiple_choice":
                    if len(q.options) != 4 or len(set(q.options)) != 4 or any(not x.strip() for x in q.options) or q.answer not in q.options:
                        raise ValueError()
                elif q.options or (args.question_type == "true_false" and q.answer not in {"true", "false"}):
                    raise ValueError()
            return {"question_type": args.question_type, **quiz.model_dump(), "citations": material}
        except (ValidationError, ValueError):
            raise DomainError(502, "QUIZ_INVALID_RESPONSE", "模型返回的题目结构、数量、答案或引用无效") from None
