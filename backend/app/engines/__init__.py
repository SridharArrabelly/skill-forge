"""Engine registry: build the set of available engines and pick one by id.

Adding a new engine (Stage 2+) is a two-line change here plus its own module —
register the class in `ENGINE_CLASSES` and it shows up in the API + UI selector
automatically. The shared skills/tools and the SSE event contract stay the same.
"""

from __future__ import annotations

from app.config import Settings
from app.engines.base import AgentEngine
from app.engines.handrolled import HandRolledEngine
from app.skill_tools import SkillToolset

# Ordered: first = default. New engines (copilot_sdk, agent_framework, foundry)
# get appended here as we build them.
ENGINE_CLASSES: list[type[AgentEngine]] = [
    HandRolledEngine,
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
