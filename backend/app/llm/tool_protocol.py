"""Validated subset of the OpenAI Chat Completions tool-call protocol."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class FunctionCall(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    name: str = Field(min_length=1, max_length=64)
    arguments: str = Field(max_length=16000)


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    id: str = Field(min_length=1, max_length=128)
    type: Literal["function"]
    function: FunctionCall


class AssistantTurn(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=16)

    def wire(self) -> dict:
        result = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            result["tool_calls"] = [call.model_dump() for call in self.tool_calls]
        return result
