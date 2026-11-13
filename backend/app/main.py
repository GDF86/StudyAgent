from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.api.knowledge_bases import router
from app.api.documents import router as documents_router
from app.api.chat import router as chat_router
from app.api.comparison import router as comparison_router
from app.api.agent import router as agent_router
from app.api.conversations import router as conversations_router
from app.services.conversations import ConversationService
from app.services.preferences import PreferenceService
from app.services.conversation_memory import ConversationMemoryService
from app.services.conversation_execution import ConversationExecutionService
from app.api.memory import router as memory_router
from app.agent.agent import AgentService
from app.core.errors import DomainError
from app.core.upload_limit import UploadLimitMiddleware
from app.core.config import Settings
from app.database.models import Base
from app.database.session import build_engine
from app.rag.embeddings import CompatibleEmbeddingProvider
from app.services.documents import DocumentService
from app.llm.provider import CompatibleLLMProvider
from app.rag.retriever import Retriever
from app.services.chat import ChatService
from sqlalchemy.orm import Session


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an isolated application; create database tables only at startup."""
    settings = settings or Settings()
    engine = build_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(engine)
        app.state.documents = DocumentService(settings, CompatibleEmbeddingProvider(settings))
        app.state.preferences = PreferenceService(engine)
        app.state.conversations = ConversationService(engine, app.state.documents.lock)
        app.state.conversation_memory = ConversationMemoryService(settings, app.state.conversations, app.state.preferences)
        app.state.execution = ConversationExecutionService(settings, app.state.conversations, app.state.conversation_memory)
        app.state.execution.recover()
        app.state.chat = ChatService(settings, Retriever(settings, app.state.documents), CompatibleLLMProvider(settings),app.state.preferences)
        app.state.agent = AgentService(settings, engine, app.state.documents, app.state.chat.retriever, app.state.chat.provider,app.state.preferences)
        with Session(engine) as session:
            app.state.documents.recover(session)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="StudyAgent Phase 7", lifespan=lifespan)
    if settings.web_demo_enabled:
        from app.core.web_demo import WebDemoMiddleware
        app.add_middleware(WebDemoMiddleware, settings=settings)
    app.state.engine = engine
    app.state.settings = settings
    app.add_middleware(UploadLimitMiddleware, limit=settings.max_upload_bytes+(65536 if settings.web_demo_enabled else 0))
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_credentials=False, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Content-Type","X-Confirm-Delete","X-Confirm-Retry"])

    @app.get('/api/runtime-capabilities')
    def runtime_capabilities():
        return {'local_real_mode':settings.web_demo_enabled,'document_writes':settings.web_demo_allow_writes if settings.web_demo_enabled else True,
                'document_api_version':2,
                'max_upload_bytes':settings.max_upload_bytes,'max_chunks':settings.max_chunks,'embedding_batch_size':settings.embedding_batch_size}

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message, "detail": exc.detail})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        codes = {404: "NOT_FOUND", 409: "KNOWLEDGE_BASE_NAME_EXISTS"}
        return JSONResponse(status_code=exc.status_code, content={"code": codes.get(exc.status_code, "HTTP_ERROR"), "message": str(exc.detail), "detail": None})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"field": ".".join(map(str, error["loc"])), "message": error["msg"]} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"code": "VALIDATION_ERROR", "message": "请求参数不正确，请检查输入或上传文件", "detail": details})

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logging.getLogger("studyagent").error("request_failed: %s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"code": "INTERNAL_ERROR", "message": "服务暂时不可用，请稍后重试", "detail": None})

    app.include_router(router)
    app.include_router(documents_router)
    app.include_router(chat_router)
    app.include_router(comparison_router)
    app.include_router(agent_router)
    app.include_router(conversations_router)
    app.include_router(memory_router)
    return app


app = create_app()
