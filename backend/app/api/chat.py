import json
from contextlib import aclosing

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.knowledge_bases import Database
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/chat", tags=["RAG Chat"])


@router.post("", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request, session: Database):
    if body.conversation_id is not None:
        request.app.state.execution.validate(body, "chat")
        return JSONResponse(await request.app.state.execution.run(request.app, body, "chat"))
    service = request.app.state.chat
    prepared = await run_in_threadpool(service.prepare, session, body)
    return await service.answer(prepared)


@router.post("/stream")
async def stream_chat(body: ChatRequest, request: Request, session: Database):
    if body.conversation_id is not None:
        request.app.state.execution.validate(body, "chat")
        async def persistent_stream():
            async with aclosing(request.app.state.execution.events(request.app, body, "chat")) as events:
                async for event in events:
                    if await request.is_disconnected():
                        break
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        return StreamingResponse(persistent_stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    service = request.app.state.chat
    prepared = await run_in_threadpool(service.prepare, session, body)

    async def stream():
        async with aclosing(service.events(prepared)) as events:
            async for event in events:
                if await request.is_disconnected():
                    break
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
