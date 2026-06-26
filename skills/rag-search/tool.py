"""rag-search skill — retrieval-augmented generation over Azure AI Search.

Tool contract (shared by every code-backed skill in skill-forge):

    TOOL: dict           # OpenAI function "parameters" JSON schema
    run(**kwargs) -> Any # the callable the agent loop invokes

Backend: Azure AI Search via the sync ``azure.search.documents.SearchClient``.
We run a semantic query against an existing index and return the top passages.
Auth is keyless-first — leave AZURE_SEARCH_API_KEY blank to use
DefaultAzureCredential (az login / managed identity).

Env:
    AZURE_SEARCH_ENDPOINT    e.g. https://<name>.search.windows.net
    SEARCH_INDEX_NAME        the index to query
    AZURE_SEARCH_API_KEY     optional; blank -> DefaultAzureCredential
    SEARCH_SEMANTIC_CONFIG   semantic configuration name (default "mtn-semantic")
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

# Retrievable fields on the index (see the index schema). content_vector is
# omitted on purpose — we return human-readable passages, not embeddings.
_SELECT = ["title", "content", "source", "meeting_date", "chunk_index"]


def _build_client(endpoint: str, index: str):
    """Create a sync SearchClient: key if provided, else DefaultAzureCredential."""
    from azure.search.documents import SearchClient

    api_key = os.environ.get("AZURE_SEARCH_API_KEY", "").strip()
    if api_key:
        from azure.core.credentials import AzureKeyCredential

        credential = AzureKeyCredential(api_key)
    else:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()

    return SearchClient(endpoint=endpoint, index_name=index, credential=credential)


def run(query: str = "", top: int = 3, **_: Any) -> dict:
    """Return retrieved passages for `query` from the Azure AI Search index."""
    query = (query or "").strip()
    if not query:
        return {"error": "rag_search requires a non-empty 'query'."}

    endpoint = os.environ.get("AZURE_SEARCH_ENDPOINT", "").strip()
    index = os.environ.get("SEARCH_INDEX_NAME", "").strip()
    if not (endpoint and index):
        return {
            "query": query,
            "error": "rag-search is not configured.",
            "hint": "Set AZURE_SEARCH_ENDPOINT and SEARCH_INDEX_NAME in .env.",
            "results": [],
        }

    semantic_config = os.environ.get("SEARCH_SEMANTIC_CONFIG", "mtn-semantic").strip()

    try:
        client = _build_client(endpoint, index)
        try:
            hits = client.search(
                search_text=query,
                query_type="semantic",
                semantic_configuration_name=semantic_config,
                select=_SELECT,
                top=top,
            )
            results = []
            for h in hits:
                results.append(
                    {
                        "title": h.get("title", ""),
                        "content": h.get("content", ""),
                        "source": h.get("source", ""),
                        "meeting_date": str(h.get("meeting_date", "") or ""),
                        "chunk_index": h.get("chunk_index"),
                        "score": h.get("@search.reranker_score")
                        or h.get("@search.score"),
                    }
                )
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001 - surface, don't crash the loop
        return {
            "query": query,
            "error": f"Azure AI Search call failed: {type(exc).__name__}: {exc}",
            "hint": (
                "Check AZURE_SEARCH_ENDPOINT / SEARCH_INDEX_NAME, the semantic "
                "config name (SEARCH_SEMANTIC_CONFIG), and that you have "
                "'Search Index Data Reader' (or run `az login`) for keyless auth."
            ),
            "results": [],
        }

    return {
        "query": query,
        "top": top,
        "index": index,
        "result_count": len(results),
        "results": results,
    }
