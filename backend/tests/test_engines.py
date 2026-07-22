"""Tests for the engine registry and the Agent Framework + Agent Skills engine."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Settings
from app.engines import EngineRegistry
from app.engines.agent_framework_skills import AgentFrameworkSkillsEngine
from app.skill_registry import SkillRegistry
from app.skill_tools import LOAD_INSTRUCTIONS_TOOL, SkillToolset


def _write_skill(root: Path, name: str, *, with_tool: bool) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\nenabled: true\n---\n\nbody",
        encoding="utf-8",
    )
    if with_tool:
        (d / "tool.py").write_text(
            'TOOL = {"type": "object", "properties": {}}\n\ndef run(**kw):\n    return {"ok": True}\n',
            encoding="utf-8",
        )


def _toolset(skills_dir: Path) -> SkillToolset:
    return SkillToolset(SkillRegistry(skills_dir).load()).build()


def _settings(skills_dir: Path, *, configured: bool) -> Settings:
    kwargs: dict = {"skills_dir": str(skills_dir)}
    if configured:
        kwargs.update(
            azure_openai_endpoint="https://example.openai.azure.com",
            azure_openai_deployment="gpt-4o",
            azure_openai_api_key="dummy-key",  # key path avoids a live credential
        )
    return Settings(**kwargs)


def test_registry_includes_agent_framework_skills(tmp_path: Path) -> None:
    _write_skill(tmp_path, "web-grounding", with_tool=True)
    settings = _settings(tmp_path, configured=True)
    registry = EngineRegistry(settings, _toolset(tmp_path))

    ids = [e.id for e in registry.all()]
    assert "agent_framework_skills" in ids
    assert "agent_framework" not in ids  # old id retired
    assert isinstance(registry.get("agent_framework_skills"), AgentFrameworkSkillsEngine)


def test_engine_unavailable_without_azure(tmp_path: Path) -> None:
    _write_skill(tmp_path, "web-grounding", with_tool=True)
    engine = AgentFrameworkSkillsEngine(_settings(tmp_path, configured=False), _toolset(tmp_path))
    assert engine.available is False
    assert "Azure OpenAI is not configured" in (engine.unavailable_reason or "")


def test_build_tools_uses_native_load_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path, "web-grounding", with_tool=True)
    _write_skill(tmp_path, "doc-skill", with_tool=False)
    engine = AgentFrameworkSkillsEngine(_settings(tmp_path, configured=True), _toolset(tmp_path))

    tools = engine._build_tools(asyncio.Queue())
    names = {t.name for t in tools}
    # Code-backed skill is exposed; the synthetic load-instructions tool is dropped
    # in favour of the provider's native load_skill.
    assert "web_grounding" in names
    assert LOAD_INSTRUCTIONS_TOOL not in names


def test_build_skills_provider_and_agent(tmp_path: Path) -> None:
    _write_skill(tmp_path, "web-grounding", with_tool=True)
    engine = AgentFrameworkSkillsEngine(_settings(tmp_path, configured=True), _toolset(tmp_path))

    provider = engine._build_skills_provider()
    assert type(provider).__name__ == "SkillsProvider"

    # The full agent (client + tools + context provider) constructs without a
    # live model call.
    agent = engine._build_agent(engine._build_tools(asyncio.Queue()), provider)
    assert agent is not None
