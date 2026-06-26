"""The single agentic loop: Reason -> Act -> Observe.

This is the whole "agent". There is exactly ONE loop and ONE model. Every
capability is a skill/tool; the model's own reasoning decides which to call each
turn. That is the entire pattern — read this file top to bottom and you've seen it.

The loop streams typed events (see app.models) so the UI can show each step:
the model reasons (Reason), we run any tool it asked for (Act), we feed the
result back (Observe), and repeat until the model answers with plain text.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Iterable
from typing import Any

from openai import AzureOpenAI

from app.config import Settings
from app.models import ChatMessage, ContentEvent, DoneEvent, ErrorEvent, ToolCallEvent
from app.skill_tools import SkillToolset

SYSTEM_PROMPT_TEMPLATE = """You are skill-forge, a single agent with a set of swappable skills.

Prefer a skill over answering from memory whenever one is relevant. Search before
guessing; ground answers in tool results and cite them. If no skill fits, just
answer directly.

To use a skill, first call `load_skill_instructions` with its name to get the full
procedure, then follow it (which may include calling that skill's own tool).

Available skills:
{catalogue}
"""


def _build_messages(system: str, history: Iterable[ChatMessage], user: str) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": system}]
    for turn in history:
        messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": user})
    return messages


class Agent:
    """Owns the Azure OpenAI client and runs the loop for each user turn."""

    def __init__(self, settings: Settings, toolset: SkillToolset) -> None:
        self.settings = settings
        self.toolset = toolset
        self._client: AzureOpenAI | None = None

    @property
    def client(self) -> AzureOpenAI:
        if self._client is None:
            if self.settings.use_entra_auth:
                # Keyless: DefaultAzureCredential (az login / managed identity).
                from azure.identity import DefaultAzureCredential, get_bearer_token_provider

                token_provider = get_bearer_token_provider(
                    DefaultAzureCredential(),
                    "https://cognitiveservices.azure.com/.default",
                )
                self._client = AzureOpenAI(
                    azure_endpoint=self.settings.azure_openai_endpoint,
                    azure_ad_token_provider=token_provider,
                    api_version=self.settings.azure_openai_api_version,
                )
            else:
                self._client = AzureOpenAI(
                    azure_endpoint=self.settings.azure_openai_endpoint,
                    api_key=self.settings.azure_openai_api_key,
                    api_version=self.settings.azure_openai_api_version,
                )
        return self._client

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT_TEMPLATE.format(catalogue=self.toolset.skill_catalogue())

    async def run(
        self, message: str, history: list[ChatMessage]
    ) -> AsyncGenerator[dict, None]:
        """Run one user turn, yielding SSE event dicts until done."""
        if not self.settings.azure_configured:
            yield ErrorEvent(
                message="Azure OpenAI is not configured. Set AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_DEPLOYMENT in .env (auth is keyless via DefaultAzureCredential — "
                "run `az login`)."
            ).model_dump()
            yield DoneEvent().model_dump()
            return

        messages = _build_messages(self.system_prompt(), history, message)
        tools = self.toolset.openai_tools()

        try:
            for event in self._loop(messages, tools):
                yield event
        except Exception as exc:  # never leak a raw stack trace to the stream
            yield ErrorEvent(message=f"Agent error: {exc}").model_dump()
        yield DoneEvent().model_dump()

    # ── The loop itself ─────────────────────────────────────────────────────

    def _loop(self, messages: list[dict], tools: list[dict]):
        """Synchronous generator of event dicts (wrapped by run())."""
        for _ in range(self.settings.max_agent_iterations):
            # ── Reason ──────────────────────────────────────────────────────
            response = self.client.chat.completions.create(
                model=self.settings.azure_openai_deployment,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
            choice = response.choices[0].message

            # No tool calls -> the model produced its final answer.
            if not choice.tool_calls:
                yield ContentEvent(text=choice.content or "").model_dump()
                return

            # Record the assistant's tool-call turn before answering them.
            messages.append(
                {
                    "role": "assistant",
                    "content": choice.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in choice.tool_calls
                    ],
                }
            )

            # ── Act + Observe ───────────────────────────────────────────────
            for tc in choice.tool_calls:
                name = tc.function.name
                args = _safe_json(tc.function.arguments)

                yield ToolCallEvent(status="start", skill=name, arguments=args).model_dump()
                result = self.toolset.call(name, args)
                yield ToolCallEvent(
                    status="result", skill=name, arguments=args, result=result
                ).model_dump()

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    }
                )
            # loop back: the model now reasons over the tool results.

        # Hit the iteration guard without a final text answer.
        yield ContentEvent(
            text="(Stopped: reached the maximum number of skill steps for this turn.)"
        ).model_dump()


def _safe_json(raw: str) -> dict[str, Any]:
    """Parse tool-call arguments, tolerating empty/invalid JSON."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        return {}
