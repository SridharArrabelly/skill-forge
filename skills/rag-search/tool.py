"""rag-search skill — retrieval-augmented generation over Azure AI Search.

Tool contract (shared by every code-backed skill in skill-forge):

    TOOL: dict           # OpenAI function "parameters" JSON schema
    run(**kwargs) -> Any # the callable the agent loop invokes

STATUS: stubbed. Returns clearly-marked placeholder passages so the loop runs
end-to-end today. Replace the body of `run` with a real Azure AI Search query
(AZURE_SEARCH_ENDPOINT / AZURE_SEARCH_API_KEY / AZURE_SEARCH_INDEX) when we
connect a live index.
"""

from __future__ import annotations

import os
from typing import Any

TOOL: dict = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "What to look up in the knowledge base.",
        },
        "top": {
            "type": "integer",
            "description": "How many passages to retrieve (default 3).",
            "minimum": 1,
            "maximum": 10,
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


def run(query: str = "", top: int = 3, **_: Any) -> dict:
    """Return retrieved passages for `query`.

    Currently a stub. The shape mirrors a real Azure AI Search response: a list
    of passages with `content`, `title`, and `source`.
    """
    query = (query or "").strip()
    if not query:
        return {"error": "rag_search requires a non-empty 'query'."}

    index = os.environ.get("SEARCH_INDEX_NAME", "")
    configured = bool(os.environ.get("AZURE_SEARCH_ENDPOINT") and index)

    return {
        "query": query,
        "top": top,
        "stub": True,
        "configured": configured,
        "index": index or "(none configured)",
        "results": [
            {
                "content": (
                    f"[STUB rag-search] Placeholder passage for {query!r}. No "
                    "Azure AI Search index is wired yet."
                ),
                "title": "stub-document",
                "source": "skill-forge://stub",
            }
        ],
    }
