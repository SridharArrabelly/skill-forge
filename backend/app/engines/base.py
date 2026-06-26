"""The engine abstraction: one interface, many orchestration backends.

skill-forge is a learning project that compares ways to drive the *same* agentic
behaviour (same skills, same tools, same UI) using different orchestration
engines. To keep those comparisons honest, every engine implements this one
interface and emits the **same** stream of SSE event dicts (see app.models):
`content`, `tool_call` (start/result), `error`, `done`. The UI never needs to
know which engine produced them.

What differs between engines is *only* how they register and invoke the shared
skill tools and how much of the loop they own — never the tools themselves and
never the event contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.config import Settings
from app.models import ChatMessage
from app.skill_tools import SkillToolset


class AgentEngine(ABC):
    """Base class for an orchestration engine.

    Subclasses set the metadata class attributes and implement `run` as an
    async generator yielding SSE event dicts.
    """

    # Stable id used in the API + UI selector (e.g. "handrolled").
    id: str = "base"
    # Human-friendly name shown in the UI dropdown.
    label: str = "Base engine"
    # One-line description of what this engine is / where it sits.
    description: str = ""

    def __init__(self, settings: Settings, toolset: SkillToolset) -> None:
        self.settings = settings
        self.toolset = toolset

    @property
    def available(self) -> bool:
        """Whether this engine can run right now (deps + config present)."""
        return True

    @property
    def unavailable_reason(self) -> str | None:
        """Why the engine is unavailable, for the UI to show. None when ready."""
        return None

    @abstractmethod
    def run(
        self, message: str, history: list[ChatMessage]
    ) -> AsyncIterator[dict]:
        """Run one user turn, yielding SSE event dicts until a `done` event."""
        raise NotImplementedError

    def info(self) -> dict:
        """Metadata for the `/api/engines` listing."""
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
        }
