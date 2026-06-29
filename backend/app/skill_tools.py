"""Turn discovered skills into callable tools for the agent loop.

Two kinds of skill become tools:

* **Code-backed** skills (folder has `tool.py`) become a real function tool. We
  import the folder's `tool.py`, read its `TOOL` argument schema, and wrap its
  `run(**kwargs)` callable. The function the model sees is named after the skill
  (`web-grounding` -> `web_grounding`) and described by the skill's `description`.

* **Every** enabled skill is also reachable through one synthetic tool,
  `load_skill_instructions(name)`, which returns the SKILL.md body on demand.
  This gives the model *progressive disclosure*: the system prompt lists each
  skill's short `description`, and the model pulls the full procedure only when
  it decides to use that skill — exactly how an instructions-only skill is used.

Keeping this separate from `skill_registry.py` means discovery stays trivially
testable and the (slightly messier) dynamic-import logic lives in one place.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.skill_registry import Skill, SkillRegistry

LOAD_INSTRUCTIONS_TOOL = "load_skill_instructions"


@dataclass
class FunctionTool:
    """A code-backed skill exposed as a callable function tool."""

    name: str  # function name the model calls (e.g. "web_grounding")
    description: str
    parameters: dict  # JSON schema for arguments
    run: Callable[..., Any]


def _import_tool_module(skill: Skill):
    """Import a skill folder's `tool.py` as an isolated module."""
    tool_path = skill.path / "tool.py"
    mod_name = f"skillforge_tool_{skill.tool_name}_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(mod_name, tool_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load tool.py for skill {skill.name!r}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillToolset:
    """Builds and dispatches the tool set for one set of enabled skills."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry
        self._functions: dict[str, FunctionTool] = {}

    def build(self) -> SkillToolset:
        """Load code-backed tools for all enabled skills. Safe to recall."""
        self._functions = {}
        for skill in self.registry.enabled():
            if not skill.code_backed:
                continue
            module = _import_tool_module(skill)
            params = getattr(module, "TOOL", None)
            run = getattr(module, "run", None)
            if not isinstance(params, dict) or not callable(run):
                raise ImportError(
                    f"Skill {skill.name!r} tool.py must define TOOL (dict) and run(**kwargs)"
                )
            self._functions[skill.tool_name] = FunctionTool(
                name=skill.tool_name,
                description=skill.description or f"The {skill.name} skill.",
                parameters=params,
                run=run,
            )
        return self

    # ── What the model sees ─────────────────────────────────────────────────

    def openai_tools(self) -> list[dict]:
        """OpenAI `tools=` array: code-backed functions + load_skill_instructions."""
        tools: list[dict] = [
            {
                "type": "function",
                "function": {
                    "name": fn.name,
                    "description": fn.description,
                    "parameters": fn.parameters,
                },
            }
            for fn in self._functions.values()
        ]
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": LOAD_INSTRUCTIONS_TOOL,
                    "description": (
                        "Load the full step-by-step instructions for one of the "
                        "available skills before using it. Call this when a skill's "
                        "short description matches the user's need."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "The skill name to load instructions for.",
                            }
                        },
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                },
            }
        )
        return tools

    def skill_catalogue(self) -> str:
        """A compact catalogue of enabled skills for the system prompt."""
        lines = []
        for skill in self.registry.enabled():
            tag = "callable tool" if skill.code_backed else "instructions"
            lines.append(f"- {skill.name} ({tag}): {skill.description}")
        return "\n".join(lines) if lines else "(no skills available)"

    # ── Dispatch (the 'Act' step) ───────────────────────────────────────────

    def call(self, name: str, arguments: dict) -> Any:
        """Execute a tool call by name. Never raises — returns an error dict."""
        if name == LOAD_INSTRUCTIONS_TOOL:
            return self._load_instructions(arguments.get("name", ""))
        fn = self._functions.get(name)
        if fn is None:
            return {"error": f"Unknown tool {name!r}."}
        try:
            return fn.run(**arguments)
        except Exception as exc:  # surface tool errors to the model, don't crash
            return {"error": f"{name} failed: {exc}"}

    def _load_instructions(self, name: str) -> dict:
        # Tolerate the model passing the tool-name form (underscores) for a skill
        # whose folder name uses hyphens, e.g. "web_grounding" -> "web-grounding".
        skill = self.registry.get(name) or self.registry.get(name.replace("_", "-"))
        if skill is None or not skill.enabled:
            return {"error": f"No enabled skill named {name!r}."}
        return {
            "name": skill.name,
            "kind": skill.kind,
            "instructions": skill.instructions,
        }
