"""Stage 3 engine: Microsoft Agent Framework with the (GA) Agent Skills provider.

Unlike every other engine here, this one has **Agent Framework own the agentic
loop** — not our hand-rolled loop (Stage 1) and not the Copilot runtime (Stages 2
and 2b). We build a native `Agent` over a chat client (`OpenAIChatClient` pointed
at *your* Azure OpenAI) and let the framework run its own tool-calling loop.

What makes this stage distinct is that it consumes the **now-stable Agent Skills
feature** (`agent_framework.SkillsProvider`) instead of our home-grown
`load_skill_instructions` tool. The provider is attached as a
`context_providers=[...]` entry and, before each run, injects:

* an **advertise** system prompt listing every discovered skill's name +
  description (progressive-disclosure stage 1), and
* the native **`load_skill`**, **`read_skill_resource`**, and
  **`run_skill_script`** tools (stages 2-4).

The *same* `skills/` folders every other engine uses are discovered by
`SkillsProvider.from_paths` — no separate authoring. MAF's parser follows the
agentskills.io spec (frontmatter `name` must equal the folder name; `name` and
`description` are read, our extra `enabled` key is ignored), which our folders
already satisfy.

How the two tool layers fit together:

* **Instructions + resources** come from the provider natively. `load_skill`
  returns a skill's `SKILL.md` body; `read_skill_resource` serves bundled
  reference files. These read-only tools run unattended (approval disabled below).
* **Callable capabilities** (`web_grounding`, `rag_search`) are still our
  code-backed `tool.py` functions, exposed here as framework `FunctionTool`s built
  from the shared `SkillToolset` — so no skill logic is duplicated and the tools
  are byte-for-byte the same ones the other engines run. We deliberately drop our
  synthetic `load_skill_instructions` tool here because the provider's `load_skill`
  replaces it.

Governance: `load_skill` / `read_skill_resource` are read-only, so we disable their
approval gate for a smooth demo; `run_skill_script` keeps its default
approval-required gate (our skills ship no scripts, so it is never usefully
invoked — but the safe default stays visible).

Auth: your Azure OpenAI deployment only — keyless via `DefaultAzureCredential`
(`az login`), the same identity Stage 1 uses, or an API key if one is set. Because
the framework talks to Azure OpenAI directly (no Copilot runtime, no prompt
encryption), **any** chat-capable deployment works here — including `gpt-4o`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from app.engines.base import AgentEngine
from app.models import ChatMessage, ContentEvent, DoneEvent, ErrorEvent, ToolCallEvent
from app.skill_tools import LOAD_INSTRUCTIONS_TOOL

# Token scope for Azure OpenAI / Cognitive Services data-plane calls.
_AOAI_SCOPE = "https://cognitiveservices.azure.com/.default"

# Sentinel pushed onto the event queue when the agent turn finishes.
_DONE = object()


class AgentFrameworkSkillsEngine(AgentEngine):
    id = "agent_framework_skills"
    label = "Agent Framework + Agent Skills"
    description = (
        "Microsoft Agent Framework owns the loop (native Agent over your Azure "
        "OpenAI) and loads the same skills through the GA SkillsProvider — native "
        "progressive disclosure (load_skill / read_skill_resource) plus the shared "
        "code-backed tools. Any Azure deployment works (no prompt encryption)."
    )

    def __init__(self, settings, toolset) -> None:
        super().__init__(settings, toolset)
        # Import is the one hard dependency; fail soft so the selector can show why.
        self._import_error: str | None = None
        try:  # noqa: SIM105
            import agent_framework  # noqa: F401
            from agent_framework import Agent, SkillsProvider  # noqa: F401
            from agent_framework.openai import OpenAIChatClient  # noqa: F401
        except Exception as exc:  # pragma: no cover - env dependent
            self._import_error = str(exc)

    # ── Availability ────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._import_error is None and self.settings.azure_configured

    @property
    def unavailable_reason(self) -> str | None:
        if self._import_error is not None:
            return (
                "agent-framework not installed. Run "
                f"`pip install agent-framework`. ({self._import_error})"
            )
        if not self.settings.azure_configured:
            return (
                "Azure OpenAI is not configured. Set AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_DEPLOYMENT in .env (auth is keyless via "
                "DefaultAzureCredential — run `az login`)."
            )
        return None

    # ── Chat client (MAF owns the loop; this is just the model backend) ───────

    def _build_client(self):
        """Build an `OpenAIChatClient` pointed at your Azure OpenAI deployment.

        Keyless by default via `DefaultAzureCredential` (same `az login` identity
        as Stage 1); falls back to an API key when `AZURE_OPENAI_API_KEY` is set.
        """
        from agent_framework.openai import OpenAIChatClient

        common = {
            "model": self.settings.azure_openai_deployment,
            "azure_endpoint": self.settings.azure_openai_endpoint,
            "api_version": self.settings.azure_openai_api_version,
        }
        if self.settings.use_entra_auth:
            from azure.identity import DefaultAzureCredential

            return OpenAIChatClient(credential=DefaultAzureCredential(), **common)
        return OpenAIChatClient(api_key=self.settings.azure_openai_api_key, **common)

    # ── Tools / instructions ────────────────────────────────────────────────

    def _build_tools(self, queue: asyncio.Queue):
        """Expose the code-backed skills as framework `FunctionTool`s.

        We reuse the shared `SkillToolset` (so `web_grounding` / `rag_search` are
        the exact same callables every engine runs) but *skip* our synthetic
        `load_skill_instructions` — the `SkillsProvider` supplies the native
        `load_skill` tool instead. Each handler emits `tool_call` SSE events and
        dispatches back into `SkillToolset.call`, so no skill logic is duplicated.
        """
        from agent_framework import FunctionTool

        def make_handler(tool_name: str):
            async def handler(**kwargs):
                args = dict(kwargs)
                queue.put_nowait(
                    ToolCallEvent(status="start", skill=tool_name, arguments=args).model_dump()
                )
                # Skills do blocking network I/O; run off the event loop.
                result = await asyncio.to_thread(self.toolset.call, tool_name, args)
                queue.put_nowait(
                    ToolCallEvent(
                        status="result", skill=tool_name, arguments=args, result=result
                    ).model_dump()
                )
                return json.dumps(result, default=str)

            return handler

        tools = []
        for spec in self.toolset.openai_tools():
            fn = spec["function"]
            if fn["name"] == LOAD_INSTRUCTIONS_TOOL:
                continue  # replaced by the provider's native load_skill
            tools.append(
                FunctionTool(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    input_model=fn.get("parameters") or {"type": "object", "properties": {}},
                    func=make_handler(fn["name"]),
                )
            )
        return tools

    def _build_skills_provider(self):
        """Attach the same `skills/` folders through the GA SkillsProvider.

        The read-only tools (`load_skill`, `read_skill_resource`) run unattended;
        `run_skill_script` keeps its default approval gate (our skills ship no
        scripts, so it is never usefully invoked).
        """
        from agent_framework import SkillsProvider

        return SkillsProvider.from_paths(
            skill_paths=str(self.settings.skills_path),
            disable_load_skill_approval=True,
            disable_read_skill_resource_approval=True,
        )

    @staticmethod
    def _instructions() -> str:
        # The SkillsProvider injects the skill catalogue + progressive-disclosure
        # guidance itself, so we keep our own instructions to general behaviour.
        return (
            "You are skill-forge, a single agent with a set of swappable skills.\n\n"
            "Prefer a skill over answering from memory whenever one is relevant. "
            "Search before guessing; ground answers in tool results and cite them. "
            "If no skill fits, just answer directly."
        )

    def _build_agent(self, tools, skills_provider):
        from agent_framework import Agent

        return Agent(
            client=self._build_client(),
            instructions=self._instructions(),
            tools=tools,
            context_providers=[skills_provider],
        )

    @staticmethod
    def _build_prompt(message: str, history: list[ChatMessage]) -> str:
        if not history:
            return message
        lines = []
        for turn in history:
            who = "User" if turn.role == "user" else "Assistant"
            lines.append(f"{who}: {turn.content}")
        lines.append(f"User: {message}")
        return (
            "Continue this conversation. Prior turns:\n\n"
            + "\n".join(lines)
            + "\n\nAssistant:"
        )

    # ── Run one turn ──────────────────────────────────────────────────────────

    async def run(
        self, message: str, history: list[ChatMessage]
    ) -> AsyncIterator[dict]:
        if not self.available:
            yield ErrorEvent(message=self.unavailable_reason or "Unavailable").model_dump()
            yield DoneEvent().model_dump()
            return

        queue: asyncio.Queue = asyncio.Queue()
        try:
            tools = self._build_tools(queue)
            agent = self._build_agent(tools, self._build_skills_provider())
        except Exception as exc:  # agent failed to build
            yield ErrorEvent(message=f"Agent Framework setup failed: {exc}").model_dump()
            yield DoneEvent().model_dump()
            return

        prompt = self._build_prompt(message, history)

        async def produce() -> None:
            # Agent Framework owns the loop: it streams assistant text and, mid-loop,
            # awaits our FunctionTool handlers (which enqueue their own tool_call
            # events). Running this in a task lets run() drain one ordered queue.
            try:
                async for update in agent.run(prompt, stream=True):
                    text = getattr(update, "text", None)
                    if text:
                        queue.put_nowait(ContentEvent(text=text).model_dump())
            except Exception as exc:
                queue.put_nowait(
                    ErrorEvent(message=f"Agent Framework error: {exc}").model_dump()
                )
            finally:
                queue.put_nowait(_DONE)

        task = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()

        yield DoneEvent().model_dump()
