"""Stage 2 engine: GitHub Copilot SDK (the runtime owns the loop).

Where it sits on the spectrum: one notch toward *managed*. Unlike Stage 1 — where
we hand-write Reason → Act → Observe in `app.agent` and call Azure OpenAI ourselves
— here the **Copilot CLI runtime owns the agentic loop**. We don't write a loop at
all. We:

1. Expose the *same* skill tools (code-backed skills + `load_skill_instructions`,
   so progressive disclosure still works) as SDK `Tool`s whose handlers call back
   into the shared `SkillToolset`. No skill logic is duplicated.
2. Open a session, send the prompt, and *listen* to the runtime's event stream,
   translating it into the same SSE event dicts every other engine emits.

Auth + models: the SDK authenticates as the **logged-in Copilot user** (no key,
no Azure OpenAI needed) and runs on Copilot's models (gpt-5.x, claude-*, …). That
is the headline contrast with Stage 1's bring-your-own Azure OpenAI.

Trade-off to notice (documented in docs/ENGINES.md): we gain a managed loop and
zero loop code, but we give up direct control of the loop and inherit the
runtime's own built-in tools, so tool selection is less predictable than Stage 1.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator

from app.engines.base import AgentEngine
from app.models import ChatMessage, ContentEvent, DoneEvent, ErrorEvent, ToolCallEvent

# Sentinel pushed onto the event queue when the session goes idle (turn done).
_DONE = object()

DEFAULT_MODEL = "gpt-5.4-mini"


class CopilotSdkEngine(AgentEngine):
    id = "copilot_sdk"
    label = "GitHub Copilot SDK"
    description = (
        "The Copilot CLI runtime owns the agentic loop; skills are exposed as SDK "
        "tools. Authenticates as your logged-in Copilot user and runs on Copilot "
        "models — no Azure OpenAI required."
    )

    def __init__(self, settings, toolset) -> None:
        super().__init__(settings, toolset)
        self._model = os.environ.get("COPILOT_SDK_MODEL", DEFAULT_MODEL)
        # Import is the one hard dependency; fail soft so the selector can show why.
        self._import_error: str | None = None
        try:  # noqa: SIM105
            import copilot  # noqa: F401
        except Exception as exc:  # pragma: no cover - env dependent
            self._import_error = str(exc)
        # A reused, lazily-started runtime client (spawning it is slow).
        self._client = None
        self._client_lock = asyncio.Lock()
        # Keep the runtime's cwd off the repo so its built-in tools don't poke it.
        self._scratch = tempfile.mkdtemp(prefix="skillforge-copilot-")

    # ── Availability ────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._import_error is None

    @property
    def unavailable_reason(self) -> str | None:
        if self._import_error is None:
            return None
        return (
            "github-copilot-sdk not installed. Run "
            "`pip install github-copilot-sdk` and `python -m copilot "
            f"download-runtime`. ({self._import_error})"
        )

    # ── Lifecycle ───────────────────────────────────────────────────────────

    async def _get_client(self):
        """Start the runtime once and reuse it across requests."""
        async with self._client_lock:
            if self._client is None:
                from copilot import CopilotClient

                client = CopilotClient(
                    log_level="error", working_directory=self._scratch
                )
                await client.start()
                self._client = client
            return self._client

    def _reset_client(self) -> None:
        # Best-effort: drop the handle so the next turn starts a fresh runtime.
        self._client = None

    # ── Tools / prompt ──────────────────────────────────────────────────────

    def _build_tools(self, queue: asyncio.Queue):
        """Build SDK Tools from the shared toolset (same names/schemas as Stage 1)."""
        from copilot.tools import Tool, ToolResult

        def make_handler(tool_name: str):
            async def handler(invocation):
                args = dict(getattr(invocation, "arguments", None) or {})
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
                return ToolResult(
                    text_result_for_llm=json.dumps(result, default=str),
                    result_type="success",
                    session_log=f"{tool_name} called",
                )

            return handler

        tools = []
        for spec in self.toolset.openai_tools():
            fn = spec["function"]
            tools.append(
                Tool(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    parameters=fn.get("parameters")
                    or {"type": "object", "properties": {}},
                    handler=make_handler(fn["name"]),
                )
            )
        return tools

    def _extra_session_kwargs(self) -> dict:
        """Extra `create_session` options. Base engine adds none.

        Subclasses override this to inject session options without duplicating the
        run loop — e.g. the BYOM engine returns a `provider` config pointing the
        runtime at your own Azure OpenAI deployment.
        """
        return {}

    def _tool_allowlist(self):
        """Restrict the session to ONLY our skill tools.

        The Copilot runtime is a full coding agent: by default the model sees a
        *merged* catalog of our custom tools plus the runtime's own built-ins
        (view/edit/shell/web/glob/grep, etc.). `available_tools` is an allowlist
        over that whole catalog, so pinning it to our custom tools makes this
        engine behave like Stage 1 — only our skills exist — which keeps the
        cross-engine comparison honest and tool selection predictable.
        """
        from copilot import ToolSet

        allow = ToolSet()
        for spec in self.toolset.openai_tools():
            allow.add_custom(spec["function"]["name"])
        return allow

    def _system_addendum(self) -> str:
        return (
            "You are skill-forge's agent. You have these skills available as tools:\n"
            f"{self.toolset.skill_catalogue()}\n\n"
            "Call `load_skill_instructions(name)` to read a skill's full procedure "
            "before using it, then call the matching skill. Answer using the skill "
            "results and keep any source citations the skill returns."
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

        from copilot.session import PermissionHandler
        from copilot.session_events import AssistantMessageDeltaData, SessionIdleData

        queue: asyncio.Queue = asyncio.Queue()
        tools = self._build_tools(queue)

        try:
            client = await self._get_client()
            session_kwargs = dict(
                model=self._model,
                on_permission_request=PermissionHandler.approve_all,
                tools=tools,
                available_tools=self._tool_allowlist(),
                streaming=True,
                system_message={"mode": "append", "content": self._system_addendum()},
            )
            # Hook for subclasses (e.g. BYOM) to add/override session options
            # such as a custom `provider` pointed at your own Azure OpenAI.
            session_kwargs.update(self._extra_session_kwargs())
            session = await client.create_session(**session_kwargs)
        except Exception as exc:  # session/runtime failed to start
            self._reset_client()
            yield ErrorEvent(message=f"Copilot SDK session failed: {exc}").model_dump()
            yield DoneEvent().model_dump()
            return

        def on_event(event) -> None:
            data = getattr(event, "data", None)
            if isinstance(data, AssistantMessageDeltaData):
                if data.delta_content:
                    queue.put_nowait(ContentEvent(text=data.delta_content).model_dump())
            elif isinstance(data, SessionIdleData):
                queue.put_nowait(_DONE)

        async with session:
            session.on(on_event)
            try:
                await session.send(self._build_prompt(message, history))
            except Exception as exc:
                yield ErrorEvent(message=f"Copilot SDK send failed: {exc}").model_dump()
                yield DoneEvent().model_dump()
                return
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                yield item

        yield DoneEvent().model_dump()
