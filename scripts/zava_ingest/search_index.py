"""Create the Zava Azure AI Search index and push chunk documents.

Index schema (`zava-benefits-index`) — fields match the `Chunk` dataclass and the
`rag-search` tool's SELECT list:

    id             key           Search-legal doc key
    title          searchable    document title (for citations + semantic ranking)
    content        searchable    the passage text
    source         filterable    original filename
    page           filterable    1-based page number (clean citations)
    chunk_index    filt/sortable global chunk ordinal
    content_vector vector(1536)  text-embedding-3-small embedding, HNSW

Retrieval the tool uses later: **hybrid** (BM25 keyword + vector similarity) with the
**semantic reranker** (`zava-semantic`) on top — the combination that gives both recall
and precision on benefits prose + comparison tables.

We deliberately do NOT attach an integrated vectorizer to the index: the skill embeds the
query itself (keyless AOAI), which avoids granting the Search service a managed-identity
role on Azure OpenAI just to run a demo.

Auth is keyless-first (DefaultAzureCredential); set AZURE_SEARCH_API_KEY to use a key.
"""

from __future__ import annotations

import os

from .chunking import Chunk
from .embeddings import EMBEDDING_DIMENSIONS

DEFAULT_INDEX_NAME = "zava-benefits-index"
SEMANTIC_CONFIG_NAME = "zava-semantic"
_VECTOR_PROFILE = "zava-vector-profile"
_HNSW_CONFIG = "zava-hnsw"


def _endpoint() -> str:
    ep = os.environ.get("AZURE_SEARCH_ENDPOINT", "").strip()
    if not ep:
        raise RuntimeError("AZURE_SEARCH_ENDPOINT is not set.")
    return ep


def _credential():
    api_key = os.environ.get("AZURE_SEARCH_API_KEY", "").strip()
    if api_key:
        from azure.core.credentials import AzureKeyCredential

        return AzureKeyCredential(api_key)
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def index_name() -> str:
    return os.environ.get("SEARCH_INDEX_NAME", DEFAULT_INDEX_NAME).strip() or DEFAULT_INDEX_NAME


def build_index(name: str) -> "object":
    """Construct the `SearchIndex` definition (fields, vector search, semantic config)."""
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchableField,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SemanticConfiguration,
        SemanticField,
        SemanticPrioritizedFields,
        SemanticSearch,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(
            name="source", type=SearchFieldDataType.String, filterable=True, facetable=True
        ),
        SimpleField(
            name="page", type=SearchFieldDataType.Int32, filterable=True, sortable=True
        ),
        SimpleField(
            name="chunk_index",
            type=SearchFieldDataType.Int32,
            filterable=True,
            sortable=True,
        ),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name=_VECTOR_PROFILE,
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name=_HNSW_CONFIG)],
        profiles=[
            VectorSearchProfile(
                name=_VECTOR_PROFILE, algorithm_configuration_name=_HNSW_CONFIG
            )
        ],
    )

    semantic_search = SemanticSearch(
        default_configuration_name=SEMANTIC_CONFIG_NAME,
        configurations=[
            SemanticConfiguration(
                name=SEMANTIC_CONFIG_NAME,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                ),
            )
        ],
    )

    return SearchIndex(
        name=name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )


def create_index(name: str | None = None, *, recreate: bool = True) -> str:
    """Create (or recreate) the index. Returns the index name."""
    from azure.search.documents.indexes import SearchIndexClient

    name = name or index_name()
    client = SearchIndexClient(endpoint=_endpoint(), credential=_credential())
    try:
        if recreate:
            try:
                client.delete_index(name)
                print(f"  deleted existing index '{name}'")
            except Exception:  # noqa: BLE001 - fine if it didn't exist
                pass
        client.create_or_update_index(build_index(name))
        print(f"  created index '{name}'")
    finally:
        client.close()
    return name


def push_chunks(
    chunks: list[Chunk], vectors: list[list[float]], *, name: str | None = None
) -> int:
    """Upload chunk documents (with their vectors) in batches. Returns count uploaded."""
    if len(chunks) != len(vectors):
        raise ValueError(
            f"chunk/vector count mismatch: {len(chunks)} chunks, {len(vectors)} vectors"
        )
    from azure.search.documents import SearchClient

    name = name or index_name()
    client = SearchClient(endpoint=_endpoint(), index_name=name, credential=_credential())
    uploaded = 0
    try:
        batch: list[dict] = []
        for chunk, vector in zip(chunks, vectors):
            doc = chunk.to_doc()
            doc["content_vector"] = vector
            batch.append(doc)
            if len(batch) >= 100:
                client.upload_documents(documents=batch)
                uploaded += len(batch)
                batch = []
        if batch:
            client.upload_documents(documents=batch)
            uploaded += len(batch)
    finally:
        client.close()
    return uploaded
