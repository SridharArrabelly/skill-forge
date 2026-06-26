"""Stage 1 engine: the hand-rolled Reason -> Act -> Observe loop.

This is a thin adapter that exposes the existing `Agent` (in app.agent) through
the shared `AgentEngine` interface. The actual loop lives in app.agent and is
deliberately left untouched — this file only does the wiring so the engine
selector and the comparison framework can treat it like any other engine.

Where it sits on the spectrum: maximum control, zero framework. You can read the
whole loop top to bottom in app.agent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.agent import Agent
from app.engines.base import AgentEngine
from app.models import ChatMessage


class HandRolledEngine(AgentEngine):
    id = "handrolled"
    label = "Hand-rolled ReAct loop"
    description = (
        "A from-scratch Reason → Act → Observe loop over Azure OpenAI. "
        "Maximum control, no framework — every step is visible in app.agent."
    )

    def __init__(self, settings, toolset) -> None:
        super().__init__(settings, toolset)
        self._agent = Agent(settings, toolset)

    @property
    def available(self) -> bool:
        return self.settings.azure_configured

    @property
    def unavailable_reason(self) -> str | None:
        if self.settings.azure_configured:
            return None
        return (
            "Azure OpenAI not configured. Set AZURE_OPENAI_ENDPOINT and "
            "AZURE_OPENAI_DEPLOYMENT in .env (keyless auth via `az login`)."
        )

    def run(
        self, message: str, history: list[ChatMessage]
    ) -> AsyncIterator[dict]:
        # Agent.run is already an async generator of SSE event dicts.
        return self._agent.run(message, history)
