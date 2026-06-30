"""Split a single markdown blob into pages.

Both Azure Content Understanding and Document Intelligence emit page markers as HTML
comments in their markdown output:

    <!-- PageBreak -->
    <!-- PageNumber="3" -->

We split on those so each chunk can later cite a real page number. If a document has
no markers (e.g. a one-page result), we return it as a single page.
"""

from __future__ import annotations

import re

from . import Page

_PAGE_BREAK = re.compile(r"<!--\s*PageBreak\s*-->", re.IGNORECASE)
_PAGE_NUMBER = re.compile(r"<!--\s*PageNumber\s*=\s*\"?(\d+)\"?\s*-->", re.IGNORECASE)
_ANY_PAGE_COMMENT = re.compile(r"<!--\s*Page(Break|Number)[^>]*-->", re.IGNORECASE)


def split_markdown_into_pages(markdown: str) -> list[Page]:
    """Turn one markdown blob into `Page`s using embedded page markers."""
    if not markdown or not markdown.strip():
        return []

    segments = _PAGE_BREAK.split(markdown)
    pages: list[Page] = []
    for i, seg in enumerate(segments, start=1):
        # Prefer an explicit PageNumber marker; fall back to sequential numbering.
        m = _PAGE_NUMBER.search(seg)
        number = int(m.group(1)) if m else i
        text = _ANY_PAGE_COMMENT.sub("", seg).strip()
        if text:
            pages.append(Page(number=number, text=text))

    if not pages:  # no markers at all → one page
        clean = _ANY_PAGE_COMMENT.sub("", markdown).strip()
        return [Page(number=1, text=clean)] if clean else []
    return pages
