"""Embeddings — vectorize chunks with Azure OpenAI (text-embedding-3-small).

Mirrors the backend's keyless-first auth (`get_bearer_token_provider` against the
Cognitive Services scope); set ``AZURE_OPENAI_API_KEY`` to use a key instead. Embeds in
batches to keep the number of round-trips low.

Env:
    AZURE_OPENAI_ENDPOINT          your Foundry/AOAI endpoint
    AZURE_OPENAI_EMBED_DEPLOYMENT  embeddings deployment name (default text-embedding-3-small)
    AZURE_OPENAI_API_VERSION       default 2024-10-21
    AZURE_OPENAI_API_KEY           optional; blank → DefaultAzureCredential
"""

from __future__ import annotations

import os

_SCOPE = "https://cognitiveservices.azure.com/.default"
_DEFAULT_DEPLOYMENT = "text-embedding-3-small"
_DEFAULT_API_VERSION = "2024-10-21"
EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small native size
_BATCH = 32


class Embedder:
    """Batched Azure OpenAI embedding client."""

    def __init__(self) -> None:
        self._endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        self._deployment = os.environ.get(
            "AZURE_OPENAI_EMBED_DEPLOYMENT", _DEFAULT_DEPLOYMENT
        ).strip()
        self._api_version = os.environ.get(
            "AZURE_OPENAI_API_VERSION", _DEFAULT_API_VERSION
        ).strip()
        self._api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        self._client = None

    def available(self) -> bool:
        return bool(self._endpoint and self._deployment)

    @property
    def deployment(self) -> str:
        return self._deployment

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        from openai import AzureOpenAI

        if self._api_key:
            self._client = AzureOpenAI(
                azure_endpoint=self._endpoint,
                api_key=self._api_key,
                api_version=self._api_version,
            )
        else:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            token_provider = get_bearer_token_provider(DefaultAzureCredential(), _SCOPE)
            self._client = AzureOpenAI(
                azure_endpoint=self._endpoint,
                azure_ad_token_provider=token_provider,
                api_version=self._api_version,
            )
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, preserving order."""
        if not texts:
            return []
        if not self.available():
            raise RuntimeError(
                "Embeddings not configured. Set AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_EMBED_DEPLOYMENT."
            )

        client = self._ensure_client()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH):
            batch = texts[start : start + _BATCH]
            resp = client.embeddings.create(model=self._deployment, input=batch)
            # API returns items with an `index`; sort to be safe, then take embeddings.
            items = sorted(resp.data, key=lambda d: d.index)
            vectors.extend(item.embedding for item in items)
        return vectors
