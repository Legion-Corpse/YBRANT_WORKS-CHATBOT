from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Confidence(str, Enum):
    HIGH = "high"
    LOW = "low"


class Source(BaseModel):
    title: str
    url: str


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source] = Field(default_factory=list)
    confidence: Confidence
    suggestions: list[str] = Field(default_factory=list)


class IngestUrlRequest(BaseModel):
    url: str = Field(min_length=1)


class IngestResponse(BaseModel):
    source_id: str
    chunks: int


class HealthResponse(BaseModel):
    status: str
    documents: int  # distinct ingested sources
    chunks: int  # same count as `documents` — OpenAI chunks server-side


class ReadyResponse(BaseModel):
    ready: bool
    models_warmed: bool
    store_reachable: bool
    llm_configured: bool
