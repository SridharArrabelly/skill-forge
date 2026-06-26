"""Stage 3 engine: Microsoft Agent Framework over the GitHub Copilot SDK (BYOM).

This stage combines two managed pieces:

* **Microsoft Agent Framework owns the agentic loop** — we don't hand-write Reason
  → Act → Observe (Stage 1) at all. We build a framework agent, register our skills
  as framework tools, and stream a turn.
* **The Copilot SDK is the model backend, in Bring-Your-Own-Model mode** — the
  framework's `GitHubCopilotAgent` drives the Copilot CLI runtime, and we point that
  runtime at *your own Azure OpenAI deployment* via a `provider` config (BYOM)
  instead of GitHub's hosted models.

So the stack here is: Agent Framework loop ▸ Copilot SDK runtime ▸ your Azure
OpenAI. It's the deliberate fusion of Stage 2b (Copilot SDK BYOM) and a
framework-owned loop — the "everything managed, but on your model" end of the
spectrum, short of a fully hosted service.

How it reuses the shared layer with zero duplication:

1. Each entry from the shared `SkillToolset.openai_tools()` (code-backed skills +
   `load_skill_instructions`, so progressive disclosure still works) becomes an
   Agent Framework `FunctionTool` built from the *explicit JSON schema* — no typed
   Python signature required. Its handler calls back into `SkillToolset.call`.
2. We construct a `GitHubCopilotAgent` with those tools and a BYOM `provider`, then
   stream a turn, translating the framework's updates + our handler callbacks into
   the same SSE event dicts every other engine emits.

Auth: this engine needs *both* a logged-in Copilot user (the runtime still
authenticates to GitHub to start) *and* Azure OpenAI for inference — keyless via
`DefaultAzureCredential` (`az login`), same identity as Stage 1.

Caveat (see `byom.py`): the Copilot SDK encrypts prompts, so only o-series and
gpt-5 family Azure deployments work. `gpt-5.4-mini` does; `gpt-4o` does not.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from app.engines.base import AgentEngine
from app.engines.byom import azure_byom_provider, enhance_byom_error, make_bearer_token
from app.models import ChatMessage, ContentEvent, DoneEvent, ErrorEvent, ToolCallEvent

# Sentinel pushed onto the event queue when the agent turn finishes.
_DONE = object()


class AgentFrameworkEngine(AgentEngine):
    id = "agent_framework"
    label = "Agent Framework + Copilot SDK (BYOM)"
    description = (
        "Microsoft Agent Framework owns the agentic loop and drives the GitHub "
        "Copilot SDK runtime in Bring-Your-Own-Model mode — pointed at your own "
        "Azure OpenAI deployment. Same skills as every engine, exposed as framework "
        "function tools."
    )

    def __init__(self, settings, toolset) -> None:
        super().__init__(settings, toolset)
        # Import is the one hard dependency; fail soft so the selector can show why.
        self._import_error: str | None = None
        try:  # noqa: SIM105
            import agent_framework  # noqa: F401
            from agent_framework.github import GitHubCopilotAgent  # noqa: F401
        except Exception as exc:  # pragma: no cover - env dependent
            self._import_error = str(exc)
        # One token cache shared across turns (keyless BYOM path).
        self._bearer = make_bearer_token()

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
                "DefaultAzureCredential — run `az login`). The Copilot runtime also "
                "needs a logged-in Copilot user."
            )
        return None

    # ── Tools / instructions ────────────────────────────────────────────────

    def _build_tools(self, queue: asyncio.Queue):
        """Build Agent Framework FunctionTools from the shared toolset.

        Each tool is built from an explicit name + description + JSON schema (no
        typed Python signature), so the model sees exactly the same tools as every
        other engine. The handler emits tool_call SSE events and dispatches to the
        shared `SkillToolset` — no skill logic is duplicated here.
        """
        from agent_framework import FunctionTool

        def make_handler(tool_name: str):
            async def handler(**kwargs):
                args = dict(kwargs)
                queue.put_nowait(
                    ToolCallEvent(
                        status="start", skill=tool_name, arguments=args
                    ).model_dump()
                )
                # Skills do blocking network I/O; run off the event loop.
                result = await asyncio.to_thread(self.toolset.call, tool_name, args)
                queue.put_nowait(
                    ToolCallEvent(
                        status="result",
                        skill=tool_name,
                        arguments=args,
                        result=result,
                    ).model_dump()
                )
                return json.dumps(result, default=str)

            return handler

        tools = []
        for spec in self.toolset.openai_tools():
            fn = spec["function"]
            tools.append(
                FunctionTool(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    input_model=fn.get("parameters")
                    or {"type": "object", "properties": {}},
                    func=make_handler(fn["name"]),
                )
            )
        return tools

    def _instructions(self) -> str:
        return (
            "You are skill-forge, a single agent with a set of swappable skills.\n\n"
            "Prefer a skill over answering from memory whenever one is relevant. "
            "Search before guessing; ground answers in tool results and cite them. "
            "If no skill fits, just answer directly.\n\n"
            "To use a skill, first call `load_skill_instructions` with its name to "
            "get the full procedure, then follow it (which may include calling that "
            "skill's own tool).\n\n"
            f"Available skills:\n{self.toolset.skill_catalogue()}"
        )

    def _build_agent(self, tools):
        """Construct a GitHubCopilotAgent with our skills + a BYOM provider.

        `default_options` carries the BYOM `provider` (Copilot runtime → your Azure
        OpenAI) and the deployment name as the wire `model`. We also approve tool
        permissions: the Copilot runtime gates custom-tool execution behind a
        permission request and *denies by default* if no handler is set, so without
        this our skills would silently come back "permission denied". Entering the
        returned async context manager spawns/owns the Copilot runtime.
        """
        from agent_framework.github import GitHubCopilotAgent
        from copilot.session import PermissionHandler

        return GitHubCopilotAgent(
            instructions=self._instructions(),
            tools=tools,
            default_options={
                "provider": azure_byom_provider(
                    self.settings, bearer_token=self._bearer
                ),
                "model": self.settings.azure_openai_deployment,
                "on_permission_request": PermissionHandler.approve_all,
            },
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

    # ── Run one turn ────────────────────────────────────────────────────────

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
            agent_cm = self._build_agent(tools)
        except Exception as exc:  # agent failed to build
            yield ErrorEvent(message=f"Agent Framework setup failed: {exc}").model_dump()
            yield DoneEvent().model_dump()
            return

        prompt = self._build_prompt(message, history)
        model = self.settings.azure_openai_deployment

        async def produce() -> None:
            # The framework owns the loop: it streams text and, mid-stream, awaits
            # our tool handlers (which enqueue their own tool_call events). Running
            # this in a task lets run() drain a single ordered queue. Entering the
            # agent context manager spawns the Copilot runtime for this turn.
            try:
                async with agent_cm as agent:
                    async for update in agent.run(prompt, stream=True):
                        text = getattr(update, "text", None)
                        if text:
                            queue.put_nowait(ContentEvent(text=text).model_dump())
            except Exception as exc:
                queue.put_nowait(
                    ErrorEvent(
                        message=f"Agent Framework error: {enhance_byom_error(model, exc)}"
                    ).model_dump()
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
