"""Durable request lifecycle around existing Chat/Agent; commit precedes done."""
import asyncio
from contextlib import aclosing
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.errors import DomainError
from app.database.models import Conversation, Message, utc_now
from app.schemas.conversation import MessageRead

log = logging.getLogger("uvicorn.error")


@dataclass
class Attempt:
    conversation_id: str
    request_id: str
    user_id: str
    assistant_id: str
    turn_no: int
    attempt_no: int
    knowledge_base_id: int
    mode: str


def error_status(code):
    if code in {"MEMORY_CONTEXT_TOO_LARGE", "MEMORY_TURN_TOO_LARGE", "RAG_CONTEXT_TOO_LARGE", "AGENT_CONTEXT_TOO_LARGE"}: return 413
    if code in {"CONVERSATION_TIMEOUT", "AGENT_TIMEOUT"}: return 504
    if code.endswith("NOT_CONFIGURED"): return 503
    if code.endswith("RATE_LIMITED"): return 429
    if code in {"AGENT_CALL_LIMIT", "AGENT_ROUND_LIMIT"}: return 422
    if code in {"REQUEST_CANCELLED", "REQUEST_INTERRUPTED"}: return 409
    if code in {"HISTORY_STORAGE_ERROR", "INTERNAL_ERROR", "AGENT_FAILED"}: return 500
    return 502


class ConversationExecutionService:
    def __init__(self, settings, conversations, memory):
        self.settings, self.conversations, self.memory = settings, conversations, memory
        self.running = {}

    def validate(self, body, mode):
        with self.conversations.transaction() as session:
            conversation = self.conversations.require_conversation(session, body.conversation_id, body.knowledge_base_id)
            if conversation.mode != mode:
                raise DomainError(409, "CONVERSATION_MODE_MISMATCH", "会话模式与当前接口不一致")

    def recover(self):
        with self.conversations.transaction() as session:
            for message in session.scalars(select(Message).where(Message.status == "pending")):
                message.status, message.error_code, message.updated_at = "interrupted", "REQUEST_INTERRUPTED", utc_now()

    @staticmethod
    def result(attempt, assistant):
        result = {"knowledge_base_id": attempt.knowledge_base_id, "answer": assistant.content,
                  "citations": assistant.citations, "conversation_id": attempt.conversation_id,
                  "request_id": attempt.request_id, "user_message_id": attempt.user_id,
                  "assistant_message_id": attempt.assistant_id, "turn_no": attempt.turn_no, "attempt_no": attempt.attempt_no}
        if attempt.mode == "chat": result["status"] = assistant.result_metadata["chat_status"]
        else: result["tool_calls"] = assistant.result_metadata["tool_calls"]
        return result

    def reserve(self, body, mode):
        self.validate(body, mode)
        cid, rid = str(body.conversation_id), str(body.request_id or uuid4())
        question = body.question if mode == "chat" else body.task
        fingerprint = sha256(json.dumps(body.model_dump(mode="json", exclude={"conversation_id", "request_id"}),
                                       sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.conversations.lock:
            if cid not in self.conversations.active:
                # Commit orphan recovery independently: rejecting a failed retry
                # below must not roll the recovered state back to pending.
                with self.conversations.transaction() as session:
                    self.conversations.require_conversation(session, cid, body.knowledge_base_id)
                    for orphan in session.scalars(select(Message).where(Message.conversation_id == cid, Message.status == "pending")):
                        orphan.status, orphan.error_code = "interrupted", "REQUEST_INTERRUPTED"
                        orphan.updated_at = utc_now()
            with self.conversations.transaction() as session:
                conversation = self.conversations.require_conversation(session, cid, body.knowledge_base_id)
                pair = session.scalars(select(Message).where(Message.conversation_id == cid, Message.request_id == rid)).all()
                if pair:
                    user = next((m for m in pair if m.role == "user"), None)
                    answer = next((m for m in pair if m.role == "assistant"), None)
                    if user is None or answer is None or user.result_metadata.get("request_fingerprint") != fingerprint:
                        raise DomainError(409, "REQUEST_ID_CONFLICT", "request_id 已用于不同请求，请使用新的标识")
                    attempt = Attempt(cid, rid, user.id, answer.id, user.turn_no, user.attempt_no, body.knowledge_base_id, mode)
                    if answer.status == "completed": return attempt, self.result(attempt, answer)
                    if answer.status == "pending":
                        raise DomainError(409, "CONVERSATION_BUSY", "该请求仍在执行，请稍后查看历史")
                    code = answer.error_code or "REQUEST_INTERRUPTED"
                    raise DomainError(error_status(code), code, "该请求已失败或停止，历史已保留；重新执行请使用新的 request_id")
                self.conversations.require_idle(cid)
                last = session.scalar(select(func.max(Message.turn_no)).where(Message.conversation_id == cid)) or 0
                number, attempt_no = last + 1, 1
                if body.regenerate_turn is not None:
                    if body.regenerate_turn != last or last == 0:
                        raise DomainError(409, "REGENERATION_NOT_LATEST", "仅支持重新生成最后一轮；较早问题请作为新问题发送")
                    previous = session.scalars(select(Message).where(Message.conversation_id == cid,
                        Message.turn_no == last, Message.role == "user").order_by(Message.attempt_no.desc())).first()
                    if previous is None or previous.content != question:
                        raise DomainError(409, "REGENERATION_INPUT_MISMATCH", "重新生成必须使用原问题")
                    number, attempt_no = last, previous.attempt_no + 1
                user = Message(id=str(uuid4()), conversation_id=cid, role="user", content=question,
                    turn_no=number, attempt_no=attempt_no, request_id=rid, status="completed", citations=[],
                    result_metadata={"request_fingerprint": fingerprint})
                answer = Message(id=str(uuid4()), conversation_id=cid, role="assistant", content="",
                    turn_no=number, attempt_no=attempt_no, request_id=rid, status="pending", citations=[], result_metadata={})
                session.add_all([user, answer])
                conversation.revision += 1
                conversation.updated_at = utc_now()
                session.flush()
                attempt = Attempt(cid, rid, user.id, answer.id, number, attempt_no, body.knowledge_base_id, mode)
            self.conversations.active[cid] = rid
        return attempt, None

    def release(self, attempt):
        with self.conversations.lock:
            if self.conversations.active.get(attempt.conversation_id) == attempt.request_id:
                self.conversations.active.pop(attempt.conversation_id, None)
                self.running.pop(attempt.conversation_id, None)

    def complete(self, attempt, result):
        with self.conversations.transaction() as session:
            conversation = self.conversations.require_conversation(session, attempt.conversation_id, attempt.knowledge_base_id)
            message = session.get(Message, attempt.assistant_id)
            if message is None or message.status != "pending":
                raise DomainError(409, "REQUEST_STATE_CONFLICT", "请求状态已改变，请刷新历史")
            if result["knowledge_base_id"] != attempt.knowledge_base_id or any(c["knowledge_base_id"] != attempt.knowledge_base_id for c in result["citations"]):
                raise DomainError(502, "INVALID_CITATION_SNAPSHOT", "结果来源与当前知识库不一致")
            # These snapshots came from the existing validated generation pipeline.
            # Keep them even if the source document was deleted after retrieval.
            message.content, message.citations, message.status = result["answer"], result["citations"], "completed"
            message.result_metadata = {"chat_status": result["status"]} if attempt.mode == "chat" else {"tool_calls": result["tool_calls"]}
            message.updated_at = conversation.updated_at = utc_now()
            conversation.revision += 1
            session.flush()
            MessageRead.model_validate(message)
            saved = self.result(attempt, message)
        return saved

    def fail(self, attempt, code, status="failed"):
        try:
            with self.conversations.transaction() as session:
                message = session.get(Message, attempt.assistant_id)
                if message is not None and message.status == "pending":
                    message.status, message.error_code, message.updated_at = status, code, utc_now()
        except DomainError:
            # Never report done; orphan recovery handles an unavailable DB on next request/startup.
            log.warning("history_failure_state_not_saved")

    def cancel(self, conversation_id, request_id):
        cid, rid = str(conversation_id), str(request_id)
        with self.conversations.transaction() as session:
            self.conversations.require_conversation(session, cid)
            answer = session.scalar(select(Message).where(Message.conversation_id == cid, Message.request_id == rid, Message.role == "assistant"))
            if answer is None: raise DomainError(404, "REQUEST_NOT_FOUND", "请求不存在")
            pending = answer.status == "pending"
        if pending and self.conversations.active.get(cid) == rid:
            task = self.running.get(cid)
            if task is not None: task.cancel()

    @staticmethod
    def prepare_chat(app, body):
        with Session(app.state.engine) as session:
            return app.state.chat.prepare(session, body)

    async def events(self, app, body, mode):
        attempt = None
        completed = False
        try:
            attempt, replay = self.reserve(body, mode)
            if replay is not None:
                # A replay must not release a different request currently in flight.
                attempt = None
                yield {"type": "done", **replay}
                return
            self.running[attempt.conversation_id] = asyncio.current_task()
            async with asyncio.timeout(self.settings.conversation_timeout_seconds):
                if mode == "chat":
                    prepared = await run_in_threadpool(self.prepare_chat, app, body)
                    if not prepared.immediate:
                        context = await self.memory.context(attempt.conversation_id, attempt.turn_no, app.state.chat.provider)
                        self.memory.inject(prepared.messages, context)
                    generator = app.state.chat.events(prepared)
                else:
                    context, messages = await app.state.agent.prepare(body)
                    history = await self.memory.context(attempt.conversation_id, attempt.turn_no, app.state.agent.provider)
                    self.memory.inject(messages, history)
                    context.memory_enabled = True
                    generator = app.state.agent.events(context, messages)
                async with aclosing(generator) as events:
                    async for event in events:
                        if event["type"] == "error":
                            self.fail(attempt, event["code"])
                            yield {**event, "status": event.get("status", error_status(event["code"]))}
                            return
                        if event["type"] == "done":
                            saved = self.complete(attempt, event)
                            completed = True
                            self.release(attempt)
                            yield {"type": "done", **saved}
                            return
                        yield event
        except (asyncio.CancelledError, GeneratorExit):
            if attempt and not completed: self.fail(attempt, "REQUEST_CANCELLED", "cancelled")
            raise
        except TimeoutError:
            if attempt: self.fail(attempt, "CONVERSATION_TIMEOUT")
            yield {"type": "error", "status": 504, "code": "CONVERSATION_TIMEOUT", "message": "会话请求超过总时限，历史已保留"}
        except DomainError as error:
            if attempt and not completed: self.fail(attempt, error.code)
            yield {"type": "error", "status": error.status, "code": error.code, "message": error.message}
        except Exception:
            if attempt and not completed: self.fail(attempt, "INTERNAL_ERROR")
            log.warning("conversation_execution_failed")
            yield {"type": "error", "status": 500, "code": "INTERNAL_ERROR", "message": "会话执行失败，请重试"}
        finally:
            if attempt: self.release(attempt)

    async def run(self, app, body, mode):
        async with aclosing(self.events(app, body, mode)) as events:
            async for event in events:
                if event["type"] == "error":
                    raise DomainError(event["status"], event["code"], event["message"])
                if event["type"] == "done": return {k:v for k,v in event.items() if k != "type"}
        raise DomainError(500, "INTERNAL_ERROR", "会话没有完成")
