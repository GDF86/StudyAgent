import asyncio
from collections.abc import AsyncIterator
from ipaddress import ip_address
import json
import logging
from typing import Protocol
from urllib.parse import urlsplit

import httpx
import ssl
import certifi

from app.llm.tool_protocol import AssistantTurn
from app.core.config import Settings
from app.core.errors import DomainError

log = logging.getLogger("uvicorn.error")
Messages = list[dict[str, str]]


class LLMProvider(Protocol):
    async def complete(self, messages: Messages) -> str: ...
    async def tool_turn(self, messages: list[dict], tools: list[dict]) -> AssistantTurn: ...
    def stream(self, messages: Messages) -> AsyncIterator[str]: ...


class CompatibleLLMProvider:
    """Configurable chat-completions provider; API keys/upstream text never leak."""

    def __init__(self, settings: Settings, transport=None):
        self.settings, self.transport = settings, transport
        self._local_ssl_context = None

    def _verification(self, trust_env):
        # Multi-turn conversations make many local requests. Loading the same CA
        # bundle per turn blocks the event loop on Windows. Reuse only the SSL
        # context (not an event-loop-bound client); retain certificate validation.
        if trust_env or self.transport is not None:
            return True
        if self._local_ssl_context is None:
            self._local_ssl_context = ssl.create_default_context(cafile=certifi.where())
        return self._local_ssl_context

    def _connection(self):
        url = self.settings.llm_base_url.rstrip("/")
        parsed = urlsplit(url)
        if not self.settings.llm_model or parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise DomainError(503, "LLM_NOT_CONFIGURED", "请在后端 .env 配置 LLM 服务地址和模型")
        try:
            local = ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = parsed.hostname.lower() == "localhost"
        headers = {}
        key = self.settings.llm_api_key.get_secret_value()
        if key:
            headers["Authorization"] = "Bearer " + key
        return url + "/chat/completions", headers, not local

    def _payload(self, messages: Messages, stream: bool):
        return {"model": self.settings.llm_model, "messages": messages, "stream": stream,
                "temperature": self.settings.llm_temperature, "max_tokens": self.settings.llm_max_tokens}

    @staticmethod
    def _check_status(response):
        if response.status_code == 429:
            raise DomainError(429, "LLM_RATE_LIMITED", "模型服务限流，请稍后重试")
        if not response.is_success:
            raise DomainError(502, "LLM_UPSTREAM_ERROR", "模型服务请求失败，请检查服务配置后重试")

    @staticmethod
    def _check_finish(reason):
        if reason is not None and reason != "stop":
            raise DomainError(502, "LLM_INCOMPLETE_RESPONSE", "模型未完整返回回答，请缩短问题或调整输出长度后重试")

    async def complete(self, messages: Messages) -> str:
        url, headers, trust_env = self._connection()
        log.info("llm_request stream=false")
        try:
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds, transport=self.transport, trust_env=trust_env, verify=self._verification(trust_env)) as client:
                    response = await client.post(url, headers=headers, json=self._payload(messages, False))
                    self._check_status(response)
                    choice = response.json()["choices"][0]
                    self._check_finish(choice.get("finish_reason"))
                    answer = choice["message"]["content"]
                    if not isinstance(answer, str) or not answer.strip() or len(answer) > self.settings.llm_max_answer_chars:
                        raise ValueError("invalid answer")
                    log.info("llm_response stream=false chars=%s", len(answer))
                    return answer
        except DomainError:
            raise
        except (httpx.TimeoutException, TimeoutError):
            raise DomainError(502, "LLM_TIMEOUT", "模型服务超时，请稍后重试") from None
        except Exception:
            raise DomainError(502, "LLM_INVALID_RESPONSE", "模型服务不可用或返回了无效响应") from None

    async def stream(self, messages: Messages) -> AsyncIterator[str]:
        url, headers, trust_env = self._connection()
        log.info("llm_request stream=true")
        try:
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds, transport=self.transport, trust_env=trust_env, verify=self._verification(trust_env)) as client:
                    async with client.stream("POST", url, headers=headers, json=self._payload(messages, True)) as response:
                        self._check_status(response)
                        if "text/event-stream" not in response.headers.get("content-type", ""):
                            raise ValueError("not SSE")
                        data_lines: list[str] = []
                        count, finished, terminal = 0, False, False
                        async for line in response.aiter_lines():
                            if line.startswith("data:"):
                                data_lines.append(line[5:].lstrip(" "))
                                if sum(map(len, data_lines)) > 262144:
                                    raise ValueError("oversize SSE event")
                            elif not line and data_lines:
                                raw, data_lines = "\n".join(data_lines), []
                                if raw == "[DONE]":
                                    terminal = True
                                    break
                                event = json.loads(raw)
                                if "error" in event:
                                    raise ValueError("upstream error")
                                choices = event.get("choices")
                                if choices == []:
                                    continue  # Optional usage event.
                                choice = choices[0]
                                self._check_finish(choice.get("finish_reason"))
                                finished = finished or choice.get("finish_reason") == "stop"
                                token = choice["delta"].get("content")
                                if token is not None:
                                    if not isinstance(token, str):
                                        raise ValueError("invalid delta")
                                    count += len(token)
                                    if count > self.settings.llm_max_answer_chars:
                                        raise ValueError("oversize answer")
                                    if token:
                                        yield token
                        if not count or not (terminal or finished) or data_lines:
                            raise ValueError("truncated SSE")
                        log.info("llm_response stream=true chars=%s", count)
        except DomainError:
            raise
        except (httpx.TimeoutException, TimeoutError):
            raise DomainError(502, "LLM_TIMEOUT", "模型服务超时，请稍后重试") from None
        except Exception:
            raise DomainError(502, "LLM_INVALID_RESPONSE", "模型流式响应异常或连接提前中断，请重试") from None

    async def tool_turn(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        """One non-streaming planning turn; intermediate reasoning is never exposed."""
        url, headers, trust_env = self._connection()
        payload = self._payload(messages, False)
        payload.update(tools=tools, tool_choice="auto")
        try:
            async with asyncio.timeout(self.settings.llm_timeout_seconds):
                async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds, transport=self.transport, trust_env=trust_env, verify=self._verification(trust_env)) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    self._check_status(response)
                    choice = response.json()["choices"][0]
                    turn = AssistantTurn.model_validate(choice["message"])
                    reason = choice.get("finish_reason")
                    if turn.tool_calls:
                        if reason != "tool_calls" or len({c.id for c in turn.tool_calls}) != len(turn.tool_calls):
                            raise ValueError("invalid tool calls")
                    elif reason != "stop" or not turn.content or not turn.content.strip():
                        raise ValueError("incomplete turn")
                    if turn.content and len(turn.content) > self.settings.llm_max_answer_chars:
                        raise ValueError("oversize content")
                    return turn
        except DomainError:
            raise
        except (httpx.TimeoutException, TimeoutError):
            raise DomainError(502, "LLM_TIMEOUT", "模型服务超时，请稍后重试") from None
        except Exception:
            raise DomainError(502, "LLM_INVALID_TOOL_RESPONSE", "模型返回了无效或不完整的工具调用响应") from None
