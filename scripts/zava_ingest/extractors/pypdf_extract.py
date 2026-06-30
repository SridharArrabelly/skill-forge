"""Local pypdf extractor (zero-setup fallback).

No Azure call: extracts text page-by-page with ``pypdf``. Tables and multi-column
layouts degrade (text gets flattened) — fine for a quick smoke run, not for
production-quality retrieval over the table-heavy benefits PDFs. For that, prefer the
``content_understanding`` (default) or ``doc_intelligence`` extractors.

Requires ``pypdf`` (added to requirements.txt).
"""

from __future__ import annotations

from pathlib import Path

from . import ExtractedDoc, Page


class PyPdfExtractor:
    name = "pypdf"

    def available(self) -> bool:
        try:
            import pypdf  # noqa: F401
        except ImportError:
            return False
        return True

    def extract(self, path: Path, *, source: str, title: str) -> ExtractedDoc:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages: list[Page] = []
        for i, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(Page(number=i, text=text))
        return ExtractedDoc(source=source, title=title, pages=pages)
