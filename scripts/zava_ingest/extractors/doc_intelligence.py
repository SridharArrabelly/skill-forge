"""Azure Document Intelligence extractor (alternate).

Uses the ``prebuilt-layout`` model with markdown output — mature and table-aware.
Requires the ``azure-ai-documentintelligence`` package (not installed by default;
``pip install azure-ai-documentintelligence`` to use this backend).

Auth is keyless-first: leave ``DOC_INTELLIGENCE_KEY`` blank to use
``DefaultAzureCredential``.

Env:
    DOC_INTELLIGENCE_ENDPOINT   e.g. https://<name>.cognitiveservices.azure.com/
    DOC_INTELLIGENCE_KEY        optional; blank → DefaultAzureCredential
"""

from __future__ import annotations

import os
from pathlib import Path

from . import ExtractedDoc, Page
from ._pagebreak import split_markdown_into_pages


class DocIntelligenceExtractor:
    name = "doc_intelligence"

    def __init__(self) -> None:
        self._endpoint = os.environ.get("DOC_INTELLIGENCE_ENDPOINT", "").strip().rstrip("/")
        self._key = os.environ.get("DOC_INTELLIGENCE_KEY", "").strip()

    def available(self) -> bool:
        return bool(self._endpoint)

    def _client(self):
        from azure.ai.documentintelligence import DocumentIntelligenceClient

        if self._key:
            from azure.core.credentials import AzureKeyCredential

            credential = AzureKeyCredential(self._key)
        else:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
        return DocumentIntelligenceClient(endpoint=self._endpoint, credential=credential)

    def extract(self, path: Path, *, source: str, title: str) -> ExtractedDoc:
        from azure.ai.documentintelligence.models import DocumentContentFormat

        if not self.available():
            raise RuntimeError(
                "Document Intelligence is not configured. Set DOC_INTELLIGENCE_ENDPOINT "
                "(or choose another EXTRACTOR)."
            )

        client = self._client()
        try:
            poller = client.begin_analyze_document(
                "prebuilt-layout",
                body=path.read_bytes(),
                output_content_format=DocumentContentFormat.MARKDOWN,
                content_type="application/octet-stream",
            )
            result = poller.result()
        finally:
            client.close()

        markdown = getattr(result, "content", "") or ""
        pages = split_markdown_into_pages(markdown)
        if not pages:
            pages = [Page(number=1, text=markdown.strip())] if markdown.strip() else []
        return ExtractedDoc(source=source, title=title, pages=pages)
