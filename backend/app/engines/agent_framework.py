"""Stage 3 engine: Microsoft Agent Framework (the framework owns the loop).

Where it sits on the spectrum: like Stage 2, we don't hand-write the Reason →
Act → Observe loop — **Microsoft Agent Framework owns it**. But unlike Stage 2,
the model behind the loop is *our own Azure OpenAI deployment* (the same backend
Stage 1 calls directly). So the clean contrast this engine isolates is purely
**"who owns the loop"**: same Azure OpenAI model as Stage 1, same skills as every
engine, but the agentic loop is now a managed framework instead of our ~30 lines.

How it reuses the shared layer with zero duplication:

1. Each entry from the shared `SkillToolset.openai_tools()` (code-backed skills +
   `load_skill_instructions`, so progressive disclosure still works) becomes an
   Agent Framework `FunctionTool` built from the *explicit JSON schema* — no typed
   Python signature required. Its handler calls back into `SkillToolset.call`.
2. We create one `Agent` over an `OpenAIChatClient` pointed at our Azure OpenAI
   resource, stream a turn, and translate the framework's updates + our handler
   callbacks into the same SSE event dicts every other engine emits.

Auth + endpoint: keyless by default (DefaultAzureCredential / `az login`), same as
Stage 1, pointed at the same `azure_endpoint`. (One repo wrinkle handled in
`_get_client`: our `.env` uses an empty `AZURE_OPENAI_API_KEY` to mean "keyless",
and the SDK reads that env var — an empty value poisons credential resolution, so
we drop it before building the keyless client.)

Trade-off to notice (documented in docs/ENGINES.md): we get a managed loop, tool
orchestration, and middleware for free, but give up the line-by-line visibility of
Stage 1's loop — and progressive disclosure is now the framework's decision, not
ours, so it may call a skill tool directly without first loading its instructions.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator

from app.engines.base import AgentEngine
from app.models import ChatMessage, ContentEvent, DoneEvent, ErrorEvent, ToolCallEvent

# Sentinel pushed onto the event queue when the agent turn finishes.
_DONE = object()

# Scope requested for keyless Azure OpenAI tokens (Cognitive Services).
_AOAI_SCOPE = "https://cognitiveservices.azure.com/.default"


class AgentFrameworkEngine(AgentEngine):
    id = "agent_framework"
    label = "Microsoft Agent Framework"
    description = (
        "Microsoft Agent Framework owns the agentic loop; skills are exposed as "
        "framework function tools built from the same schemas. Runs on your own "
        "Azure OpenAI deployment (same model backend as the hand-rolled loop)."
    )

    def __init__(self, settings, toolset) -> None:
        super().__init__(settings, toolset)
        # Import is the one hard dependency; fail soft so the selector can show why.
        self._import_error: str | None = None
        try:  # noqa: SIM105
            import agent_framework  # noqa: F401
            from agent_framework.openai import OpenAIChatClient  # noqa: F401
        except Exception as exc:  # pragma: no cover - env dependent
            self._import_error = str(exc)
        # A lazily-built, reused chat client (it manages token refresh for us).
        self._client = None
        self._client_lock = asyncio.Lock()

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

    # ── Chat client (built once, reused) ────────────────────────────────────

    async def _get_client(self):
        """Build the Agent Framework chat client once and reuse it.

        This mirrors the simple, documented Agent Framework pattern:
        `OpenAIChatClient(model=..., azure_endpoint=..., credential=...)`.

        Two repo-specific wrinkles, both handled here:

        * `OpenAIChatClient` is a *Responses API* client, which needs a recent API
          version. We deliberately DON'T pass `api_version` so the SDK uses its
          Responses-compatible default — pinning the older `2024-10-21` we use for
          Stage 1's chat-completions path makes the Responses endpoint reject the
          request ("API version not supported").
        * Our `.env` sets an *empty* `AZURE_OPENAI_API_KEY` to signal keyless auth.
          The SDK reads that env var, and an empty value poisons credential
          resolution (it commits to key-auth, then fails with "Missing
          credentials"). So when keyless we drop the blank var before building the
          client. A real key is left untouched and used directly.
        """
        async with self._client_lock:
            if self._client is not None:
                return self._client

            from agent_framework.openai import OpenAIChatClient

            endpoint = self.settings.azure_openai_endpoint
            model = self.settings.azure_openai_deployment

            if self.settings.use_entra_auth:
                from azure.identity import DefaultAzureCredential

                # Drop the empty AZURE_OPENAI_API_KEY so the SDK takes the
                # token-provider path instead of failing on a blank key.
                if not os.environ.get("AZURE_OPENAI_API_KEY"):
                    os.environ.pop("AZURE_OPENAI_API_KEY", None)
                self._client = OpenAIChatClient(
                    model=model,
                    azure_endpoint=endpoint,
                    credential=DefaultAzureCredential(),
                )
            else:
                key = self.settings.azure_openai_api_key
                self._client = OpenAIChatClient(
                    model=model,
                    azure_endpoint=endpoint,
                    api_key=(lambda: key),
                )
            return self._client

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
            client = await self._get_client()
            tools = self._build_tools(queue)
            agent = client.as_agent(
                name="skill-forge",
                instructions=self._instructions(),
                tools=tools,
            )
        except Exception as exc:  # client/agent failed to build
            self._client = None
            yield ErrorEvent(message=f"Agent Framework setup failed: {exc}").model_dump()
            yield DoneEvent().model_dump()
            return

        prompt = self._build_prompt(message, history)

        async def produce() -> None:
            # The framework owns the loop: it streams text and, mid-stream, awaits
            # our tool handlers (which enqueue their own tool_call events). Running
            # this in a task lets run() drain a single ordered queue.
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
