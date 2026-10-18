import json
from contextlib import aclosing
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from app.schemas.agent import AgentRequest, AgentResponse

router = APIRouter(prefix="/api/agent", tags=["Agent"])


@router.post("/run", response_model=AgentResponse)
async def run_agent(body: AgentRequest, request: Request):
    if body.conversation_id is not None:
        request.app.state.execution.validate(body, "agent")
        return JSONResponse(await request.app.state.execution.run(request.app, body, "agent"))
    service = request.app.state.agent
    return await service.run(*await service.prepare(body))


@router.post("/stream")
async def stream_agent(body: AgentRequest, request: Request):
    if body.conversation_id is not None:
        request.app.state.execution.validate(body, "agent")
        async def persistent_stream():
            async with aclosing(request.app.state.execution.events(request.app, body, "agent")) as events:
                async for event in events:
                    if await request.is_disconnected():
                        break
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        return StreamingResponse(persistent_stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    service = request.app.state.agent
    context, messages = await service.prepare(body)

    async def stream():
        async with aclosing(service.events(context, messages)) as events:
            async for event in events:
                if await request.is_disconnected():
                    break
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
