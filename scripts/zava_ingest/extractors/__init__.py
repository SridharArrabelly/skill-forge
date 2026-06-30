"""Pluggable document extractors — the swappable layer of the pipeline.

Every extractor turns a source file into an :class:`ExtractedDoc` (a list of
:class:`Page` objects carrying text/markdown). The rest of the pipeline — chunking,
embedding, indexing — is identical regardless of which extractor produced the pages.
This mirrors skill-forge's *engine* pattern: one interface, several backends.

Backends:
    * ``content_understanding`` (default) — Azure AI Content Understanding GA,
      ``prebuilt-documentSearch`` analyzer. Lives in your Foundry resource; great
      tables; multimodal-ready.
    * ``doc_intelligence`` — Azure Document Intelligence ``prebuilt-layout``. Mature,
      table-aware.
    * ``pypdf`` — zero-setup local fallback (no Azure call). Tables degrade; fine for
      a quick smoke run.

Select with the ``EXTRACTOR`` env var (or the ``--extractor`` CLI flag). Default is
``content_understanding``; markdown files (``.md``) always bypass cloud extraction and
are read directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Page:
    """One page of extracted text (markdown where the extractor supports it)."""

    number: int  # 1-based page number, for citations
    text: str


@dataclass
class ExtractedDoc:
    """The result of extracting one source file."""

    source: str  # original filename, e.g. "PerksPlus.pdf"
    title: str  # human title, e.g. "PerksPlus"
    pages: list[Page] = field(default_factory=list)

    @property
    def nonempty_pages(self) -> list[Page]:
        return [p for p in self.pages if p.text and p.text.strip()]


class Extractor(Protocol):
    """A document extractor strategy."""

    name: str

    def available(self) -> bool:
        """True if this extractor is configured/usable in the current environment."""
        ...

    def extract(self, path: Path, *, source: str, title: str) -> ExtractedDoc:
        """Crack `path` into an `ExtractedDoc`."""
        ...


# ── Markdown passthrough ────────────────────────────────────────────────────
# `.md` files don't need a cloud cracker — read them as a single page. This keeps
# Zava_Company_Overview.md cheap and exact regardless of the selected extractor.

def read_markdown(path: Path, *, source: str, title: str) -> ExtractedDoc:
    text = path.read_text(encoding="utf-8")
    return ExtractedDoc(source=source, title=title, pages=[Page(number=1, text=text)])


# ── Selector ────────────────────────────────────────────────────────────────

_DEFAULT = "content_understanding"


def get_extractor(name: str | None = None) -> Extractor:
    """Return the requested extractor (env ``EXTRACTOR`` if `name` is None)."""
    choice = (name or os.environ.get("EXTRACTOR", _DEFAULT)).strip().lower()

    if choice in ("content_understanding", "cu"):
        from .content_understanding import ContentUnderstandingExtractor

        return ContentUnderstandingExtractor()
    if choice in ("doc_intelligence", "di"):
        from .doc_intelligence import DocIntelligenceExtractor

        return DocIntelligenceExtractor()
    if choice in ("pypdf", "local"):
        from .pypdf_extract import PyPdfExtractor

        return PyPdfExtractor()

    raise ValueError(
        f"Unknown EXTRACTOR {choice!r}. Use one of: "
        "content_understanding, doc_intelligence, pypdf."
    )


__all__ = [
    "Page",
    "ExtractedDoc",
    "Extractor",
    "read_markdown",
    "get_extractor",
]
