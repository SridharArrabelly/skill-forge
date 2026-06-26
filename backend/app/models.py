"""Wire models: the chat request and the Server-Sent Event (SSE) payloads.

The loop streams a small, explicit set of event types so the UI can render
exactly what the agent is doing at each step of Reason → Act → Observe.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """One turn in the conversation."""

    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """Body of POST /api/chat."""

    message: str
    # Prior turns, oldest-first. The client owns history in this minimal cut.
    history: list[ChatMessage] = Field(default_factory=list)
    # Which orchestration engine to run this turn through. None -> server default.
    engine: str | None = None


# ── SSE events ──────────────────────────────────────────────────────────────
# Every event is `{"type": ..., ...}`. The UI switches on `type`.


class ContentEvent(BaseModel):
    """A chunk of the assistant's final natural-language answer (streamed)."""

    type: Literal["content"] = "content"
    text: str


class ToolCallEvent(BaseModel):
    """The agent decided to invoke a skill (the 'Act' step).

    Emitted once when the call starts (status='start') and once when it
    finishes (status='result'), so the UI can show a live chip per skill.
    """

    type: Literal["tool_call"] = "tool_call"
    status: Literal["start", "result"]
    skill: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any | None = None


class ErrorEvent(BaseModel):
    """Something went wrong; the stream ends after this."""

    type: Literal["error"] = "error"
    message: str


class DoneEvent(BaseModel):
    """Terminal event — the turn is complete."""

    type: Literal["done"] = "done"
