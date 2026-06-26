"""Engine registry: build the set of available engines and pick one by id.

Adding a new engine (Stage 2+) is a two-line change here plus its own module —
register the class in `ENGINE_CLASSES` and it shows up in the API + UI selector
automatically. The shared skills/tools and the SSE event contract stay the same.
"""

from __future__ import annotations

from app.config import Settings
from app.engines.agent_framework import AgentFrameworkEngine
from app.engines.base import AgentEngine
from app.engines.copilot_sdk import CopilotSdkEngine
from app.engines.copilot_sdk_byom import CopilotSdkByomEngine
from app.engines.handrolled import HandRolledEngine
from app.skill_tools import SkillToolset

# Ordered: first = default. Engines show up in the API + UI selector in this order.
#   Stage 1  handrolled        — you own the loop, your Azure OpenAI
#   Stage 2  copilot_sdk       — Copilot runtime owns the loop, Copilot models
#   Stage 2b copilot_sdk_byom  — Copilot runtime owns the loop, your Azure OpenAI
#   Stage 3  agent_framework   — Agent Framework loop ▸ Copilot SDK (BYOM) ▸ your model
ENGINE_CLASSES: list[type[AgentEngine]] = [
    HandRolledEngine,
    CopilotSdkEngine,
    CopilotSdkByomEngine,
    AgentFrameworkEngine,
]


class EngineRegistry:
    """Instantiates one of each engine over the shared settings + toolset."""

    def __init__(self, settings: Settings, toolset: SkillToolset) -> None:
        self._engines: dict[str, AgentEngine] = {
            cls.id: cls(settings, toolset) for cls in ENGINE_CLASSES
        }

    @property
    def default_id(self) -> str:
        return ENGINE_CLASSES[0].id

    def all(self) -> list[AgentEngine]:
        return list(self._engines.values())

    def get(self, engine_id: str | None) -> AgentEngine:
        """Return the requested engine, or the default when id is unknown/None."""
        if engine_id and engine_id in self._engines:
            return self._engines[engine_id]
        return self._engines[self.default_id]

    def infos(self) -> list[dict]:
        return [e.info() for e in self._engines.values()]


__all__ = ["AgentEngine", "EngineRegistry", "ENGINE_CLASSES"]
