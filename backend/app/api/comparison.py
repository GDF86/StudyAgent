from time import perf_counter
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from app.api.knowledge_bases import Database
from app.core.errors import DomainError
from app.rag.prompts import build_messages
from app.schemas.chat import ChatResponse
from app.schemas.comparison import CompareRequest, CompareResponse, CompareRun
from app.services.chat import PreparedChat

router = APIRouter(prefix="/api/rag", tags=["RAG comparison"])


@router.post("/compare", response_model=CompareResponse)
async def compare(body: CompareRequest, request: Request, session: Database):
    """Explicitly compare both paths without changing global settings or history."""
    service = request.app.state.chat
    runs = []
    for mode in ("basic", "reranked"):
        start = perf_counter()
        cfg=request.app.state.settings
        if mode=='reranked' and (cfg.web_demo_enabled or not cfg.reranker_base_url or not cfg.reranker_model):
            runs.append(CompareRun(mode=mode,error_code='RERANKER_NOT_ENABLED' if cfg.web_demo_enabled else 'RERANKER_NOT_CONFIGURED',error_message='真实Reranker未启用或未获费用授权；仅展示实际Basic检索，不执行重排或额外向量化',elapsed_ms=0))
            continue
        try:
            result = await run_in_threadpool(service.retriever.retrieve, session, body.knowledge_base_id,
                                            body.question, body.top_k, rerank=mode == "reranked")
            answer = None
            if body.include_answer:
                if result.citations:
                    answer = await service.answer(PreparedChat(body.knowledge_base_id, result.citations,
                                                              build_messages(body.question, result.citations)))
                else:
                    answer = ChatResponse(knowledge_base_id=body.knowledge_base_id, citations=[],
                        answer="未检索到可用于回答的资料。", status="no_relevant_content" if result.has_ready_documents else "empty_knowledge_base")
            run = CompareRun(mode=mode, citations=result.citations, answer=answer, elapsed_ms=(perf_counter()-start)*1000)
        except DomainError as error:
            run = CompareRun(mode=mode, error_code=error.code, error_message=error.message, elapsed_ms=(perf_counter()-start)*1000)
        runs.append(run)
    return CompareResponse(runs=runs, candidate_k=request.app.state.settings.reranker_candidate_k)
