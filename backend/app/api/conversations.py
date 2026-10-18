from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from app.schemas.conversation import (ConversationCreate, ConversationPage, ConversationQuery,
    ConversationRead, ConversationUpdate, MessagePage, MessageQuery, ScopeQuery)

router = APIRouter(prefix="/api/conversations", tags=["History"])


@router.post("", response_model=ConversationRead, status_code=201)
def create_conversation(body: ConversationCreate, request: Request):
    return request.app.state.conversations.create(body)


@router.get("", response_model=ConversationPage)
def list_conversations(request: Request, query: Annotated[ConversationQuery, Query()]):
    return request.app.state.conversations.list_conversations(query)


@router.get("/{conversation_id}", response_model=ConversationRead)
def get_conversation(conversation_id: UUID, request: Request, query: Annotated[ScopeQuery, Query()]):
    return request.app.state.conversations.get(conversation_id, query.knowledge_base_id)


@router.get("/{conversation_id}/messages", response_model=MessagePage)
def list_messages(conversation_id: UUID, request: Request, query: Annotated[MessageQuery, Query()]):
    return request.app.state.conversations.messages(conversation_id, query)


@router.put("/{conversation_id}", response_model=ConversationRead)
def rename_conversation(conversation_id: UUID, body: ConversationUpdate, request: Request,
                        query: Annotated[ScopeQuery, Query()]):
    return request.app.state.conversations.rename(conversation_id, body, query.knowledge_base_id)


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, request: Request, query: Annotated[ScopeQuery, Query()]):
    request.app.state.conversations.delete(conversation_id, query.knowledge_base_id)
    return Response(status_code=204)


@router.post("/{conversation_id}/requests/{request_id}/cancel", status_code=204)
async def cancel_request(conversation_id: UUID, request_id: UUID, request: Request):
    request.app.state.execution.cancel(conversation_id, request_id)
    return Response(status_code=204)
