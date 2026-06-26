"""web-grounding skill — live web answers via WebIQ.

Tool contract (shared by every code-backed skill in skill-forge):

    TOOL: dict           # OpenAI function "parameters" JSON schema
    run(**kwargs) -> Any # the callable the agent loop invokes

The function name + description the model sees come from SKILL.md
(`name`, `description`); this file owns the argument schema and the code.

Backend: the official `webiq` Python SDK (``client.web.search``).
Auth is keyless-first — leave WEBIQ_API_KEY blank to use Entra ID via
DefaultAzureCredential (az login / managed identity). Set WEBIQ_API_KEY only
if you want key-based auth.

Env:
    WEBIQ_API_KEY    optional; when blank we use DefaultAzureCredential
    WEBIQ_REGION     market/region for results (default "us")
    WEBIQ_MAX_RESULTS  default 5
    WEBIQ_MAX_LENGTH   per-result content cap in chars (default 2000)
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
        "max_results": {
            "type": "integer",
            "description": "How many web results to fetch (default 5).",
            "minimum": 1,
            "maximum": 10,
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


def _build_client():
    """Create a WebIQ SDK client: key if provided, else DefaultAzureCredential."""
    from webiq import WebIQClient

    api_key = os.environ.get("WEBIQ_API_KEY", "").strip()
    if api_key:
        return WebIQClient(api_key=api_key)

    from azure.identity import DefaultAzureCredential

    return WebIQClient(credential=DefaultAzureCredential())


def run(query: str = "", max_results: int | None = None, **_: Any) -> dict:
    """Return web-grounding results for `query`.

    Calls WebIQ's web search and returns a short text context plus a list of
    `{title, url}` citations. On any failure we return an `error` field so the
    agent loop keeps going and can tell the user honestly.
    """
    query = (query or "").strip()
    if not query:
        return {"error": "web_grounding requires a non-empty 'query'."}

    region = os.environ.get("WEBIQ_REGION", "us").strip() or "us"
    top = int(max_results or os.environ.get("WEBIQ_MAX_RESULTS", "5") or 5)
    max_length = int(os.environ.get("WEBIQ_MAX_LENGTH", "2000") or 2000)

    try:
        from webiq.types import ContentFormat

        client = _build_client()
        try:
            response = client.web.search(
                query,
                max_results=top,
                language="en",
                region=region,
                content_format=ContentFormat.text,
                max_length=max_length,
            )
        finally:
            if hasattr(client, "close"):
                client.close()
    except Exception as exc:  # noqa: BLE001 - surface, don't crash the loop
        return {
            "query": query,
            "error": f"WebIQ call failed: {type(exc).__name__}: {exc}",
            "hint": (
                "Check WEBIQ_API_KEY (or `az login` for keyless), WEBIQ_REGION, "
                "and that the `webiq` package is installed."
            ),
            "citations": [],
        }

    web_results = getattr(response, "webResults", None) or []
    results = []
    for r in web_results:
        results.append(
            {
                "title": getattr(r, "title", "") or "",
                "url": getattr(r, "url", "") or "",
                "content": getattr(r, "content", "") or "",
            }
        )

    citations = [{"title": r["title"], "url": r["url"]} for r in results if r["url"]]
    text = "\n\n".join(
        f"[{i + 1}] {r['title']}\n{r['content']}".strip()
        for i, r in enumerate(results)
        if r["content"] or r["title"]
    )

    return {
        "query": query,
        "region": region,
        "result_count": len(results),
        "text": text or "(no web results returned)",
        "citations": citations,
    }
