"""Zava index ingestion — build an Azure AI Search index from the Zava HR corpus.

This package is a small, readable, *push-model* indexing pipeline:

    download → extract (pluggable) → chunk → embed → push

The one design idea worth calling out: the **extractor is a pluggable strategy**,
exactly like skill-forge's engine layer. The same pipeline runs whether you crack
PDFs with Azure AI Content Understanding (default), Azure Document Intelligence, or a
zero-setup local pypdf fallback — only the extractor swaps. See ``extractors``.
"""
