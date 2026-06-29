"""Tests for skill discovery + frontmatter parsing."""

from __future__ import annotations

from pathlib import Path

from app.skill_registry import SkillRegistry, parse_frontmatter
from app.skill_tools import SkillToolset


def test_parse_frontmatter_extracts_fields_and_body() -> None:
    text = "---\nname: demo\ndescription: a demo\nenabled: false\n---\n\n## Instructions\nbody\n"
    front, body = parse_frontmatter(text)
    assert front == {"name": "demo", "description": "a demo", "enabled": False}
    assert body.strip() == "## Instructions\nbody"


def test_parse_frontmatter_without_block_returns_original() -> None:
    text = "no frontmatter here"
    front, body = parse_frontmatter(text)
    assert front == {}
    assert body == text


def _write_skill(root: Path, name: str, body: str, with_tool: bool) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    if with_tool:
        (d / "tool.py").write_text("TOOL = {}\n\ndef run(**kw):\n    return {}\n", encoding="utf-8")


def test_registry_discovers_skills_and_kinds(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "code-skill",
        "---\nname: code-skill\ndescription: d\nenabled: true\n---\n\nbody",
        with_tool=True,
    )
    _write_skill(
        tmp_path,
        "doc-skill",
        "---\nname: doc-skill\ndescription: d\nenabled: true\n---\n\nbody",
        with_tool=False,
    )
    _write_skill(
        tmp_path,
        "off-skill",
        "---\nname: off-skill\ndescription: d\nenabled: false\n---\n\nbody",
        with_tool=False,
    )

    reg = SkillRegistry(tmp_path).load()

    assert {s.name for s in reg.all()} == {"code-skill", "doc-skill", "off-skill"}
    assert {s.name for s in reg.enabled()} == {"code-skill", "doc-skill"}

    code = reg.get("code-skill")
    doc = reg.get("doc-skill")
    assert code is not None and code.code_backed and code.kind == "code-backed"
    assert doc is not None and not doc.code_backed and doc.kind == "instructions-only"
    assert code.tool_name == "code_skill"


def test_registry_ignores_dirs_without_skill_md(tmp_path: Path) -> None:
    (tmp_path / "not-a-skill").mkdir()
    (tmp_path / "not-a-skill" / "readme.txt").write_text("nope", encoding="utf-8")
    reg = SkillRegistry(tmp_path).load()
    assert reg.all() == []


def test_load_instructions_tolerates_underscore_name(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "web-grounding",
        "---\nname: web-grounding\ndescription: d\nenabled: true\n---\n\nbody",
        with_tool=True,
    )
    toolset = SkillToolset(SkillRegistry(tmp_path).load()).build()

    # Model passes the tool-name (underscore) form; should resolve to the hyphen skill.
    res = toolset.call("load_skill_instructions", {"name": "web_grounding"})
    assert res.get("name") == "web-grounding"
    assert "error" not in res
    # Exact hyphen name still works; an unknown name still errors.
    assert toolset.call("load_skill_instructions", {"name": "web-grounding"})["name"] == "web-grounding"
    assert "error" in toolset.call("load_skill_instructions", {"name": "nope"})
