"""web-grounding skill — live web answers via WorkIQ / WebIQ.

Tool contract (shared by every code-backed skill in skill-forge):

    TOOL: dict           # OpenAI function "parameters" JSON schema
    run(**kwargs) -> Any # the callable the agent loop invokes

The function name + description the model sees come from SKILL.md
(`name`, `description`); this file owns the argument schema and the code.

STATUS: stubbed. It returns clearly-marked placeholder data so the end-to-end
loop works today. Swap the body of `run` for a real WorkIQ web-grounding call
(using WORKIQ_ENDPOINT / WORKIQ_API_KEY) when we wire it together.
"""

from __future__ import annotations

import os
from typing import Any

# JSON schema for the tool's arguments (OpenAI "function.parameters" shape).
TOOL: dict = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The focused web search query to ground the answer.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


def run(query: str = "", **_: Any) -> dict:
    """Return web-grounding results for `query`.

    Currently a stub. The shape mirrors what a real implementation would
    return: a short text summary plus a list of `{title, url}` citations.
    """
    query = (query or "").strip()
    if not query:
        return {"error": "web_grounding requires a non-empty 'query'."}

    configured = bool(os.environ.get("WEBIQ_API_KEY"))

    return {
        "query": query,
        "stub": True,
        "configured": configured,
        "text": (
            f"[STUB web-grounding] No live web call is wired yet, so this is "
            f"placeholder context for the query: {query!r}. "
            "Tell the user the web-grounding skill is not yet connected to WebIQ."
        ),
        "citations": [],
    }
