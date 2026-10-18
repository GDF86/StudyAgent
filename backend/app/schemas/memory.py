"""One whitelist defines defaults and allowed values for HTTP and internal callers."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

PreferenceKey = Literal["answer_style", "learning_goal", "preferred_language"]


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer_style: Literal["concise", "detailed"] = "concise"
    learning_goal: Literal["general", "exam", "research"] = "general"
    preferred_language: Literal["zh-CN", "en"] = "zh-CN"


class PreferenceWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str = Field(min_length=1, max_length=32)


class PreferenceResponse(BaseModel):
    preferences: Preferences
    defaults: Preferences
    overrides: dict[PreferenceKey, str]
