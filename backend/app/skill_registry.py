"""Skill registry: discover skill folders and read their SKILL.md.

A *skill* is just a folder under the skills directory containing a `SKILL.md`
file with YAML frontmatter:

    ---
    name: web-grounding
    description: When to use this skill (the routing signal).
    enabled: true
    ---

    ## Instructions
    Procedural body the agent can load on demand.

A skill is **code-backed** when its folder also contains a `tool.py`; otherwise
it is **instructions-only**. This module only handles *discovery + parsing* —
turning skills into callable tools lives in `skill_tools.py`, keeping each
concern small and testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# Matches a leading YAML frontmatter block:  ---\n ... \n---\n
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split SKILL.md into (frontmatter_dict, body).

    Returns ({}, text) when there is no valid frontmatter block.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        front = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(front, dict):
        return {}, text
    return front, text[match.end():]


@dataclass(frozen=True)
class Skill:
    """One discovered skill."""

    name: str
    description: str
    instructions: str  # the SKILL.md body (without frontmatter)
    path: Path
    enabled: bool = True

    @property
    def code_backed(self) -> bool:
        """True when the folder ships a `tool.py` (a real callable tool)."""
        return (self.path / "tool.py").is_file()

    @property
    def kind(self) -> str:
        return "code-backed" if self.code_backed else "instructions-only"

    @property
    def tool_name(self) -> str:
        """Stable identifier usable as an OpenAI tool/function name."""
        return self.name.replace("-", "_")


def _skill_from_dir(skill_dir: Path) -> Skill | None:
    """Build a Skill from a folder, or None if it has no readable SKILL.md."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None

    front, body = parse_frontmatter(text)
    name = str(front.get("name") or skill_dir.name).strip()
    return Skill(
        name=name,
        description=str(front.get("description", "")).strip(),
        instructions=body.strip(),
        path=skill_dir,
        enabled=bool(front.get("enabled", True)),
    )


class SkillRegistry:
    """In-memory catalogue of skills loaded from a directory."""

    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir
        self._skills: dict[str, Skill] = {}

    def load(self) -> SkillRegistry:
        """(Re)scan the skills directory. Safe to call repeatedly."""
        self._skills = {}
        if not self.skills_dir.is_dir():
            return self
        for child in sorted(self.skills_dir.iterdir()):
            if not child.is_dir():
                continue
            skill = _skill_from_dir(child)
            if skill is not None:
                self._skills[skill.name] = skill
        return self

    def all(self) -> list[Skill]:
        """Every discovered skill, enabled or not."""
        return list(self._skills.values())

    def enabled(self) -> list[Skill]:
        """Only skills with `enabled: true`."""
        return [s for s in self._skills.values() if s.enabled]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)
