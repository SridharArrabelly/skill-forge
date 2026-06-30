"""Page-aware chunking for the Zava corpus.

Strategy (the proven default for benefits/HR PDFs):

* **Page-aware** — a chunk never crosses a page boundary, so every chunk carries one
  real page number for citations.
* **~500 tokens per chunk, ~10% overlap** — big enough to keep a benefit clause whole,
  small enough for precise retrieval; the overlap stops answers falling through the
  seams between chunks.
* **Sentence-boundary splits, tables kept intact** — we pack whole sentences, and treat
  a markdown table as one atomic unit so comparison tables (the highest-value content)
  never get cut mid-row.

Token counts use a lightweight ~4-chars-per-token estimate (no tiktoken dependency) —
accurate enough for sizing chunks; embeddings handle the exact tokenization later.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .extractors import ExtractedDoc

# Chunk sizing, in estimated tokens.
TARGET_TOKENS = 500
OVERLAP_TOKENS = 50
MIN_TOKENS = 12  # drop trivial fragments (page numbers, stray headers)
_CHARS_PER_TOKEN = 4


@dataclass
class Chunk:
    """One indexable passage. Field names match the Azure AI Search index schema."""

    id: str
    title: str
    content: str
    source: str
    page: int
    chunk_index: int

    def to_doc(self) -> dict:
        return asdict(self)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


# ── Splitting helpers ───────────────────────────────────────────────────────

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


def _split_into_units(text: str) -> list[str]:
    """Split page text into atomic units: whole sentences, with markdown tables kept whole."""
    units: list[str] = []
    # Group consecutive table lines into one atomic block; split prose into sentences.
    buffer: list[str] = []
    in_table = False

    def flush_prose(prose: str) -> None:
        prose = prose.strip()
        if not prose:
            return
        for sentence in _SENTENCE_END.split(prose):
            s = sentence.strip()
            if s:
                units.append(s)

    for line in text.splitlines():
        if _is_table_line(line):
            if not in_table:
                flush_prose("\n".join(buffer))
                buffer = []
                in_table = True
            buffer.append(line)
        else:
            if in_table:
                units.append("\n".join(buffer).strip())  # the table, atomic
                buffer = []
                in_table = False
            buffer.append(line)
    # flush trailing buffer
    if in_table:
        units.append("\n".join(buffer).strip())
    else:
        flush_prose("\n".join(buffer))
    return [u for u in units if u]


def _pack(units: list[str]) -> list[str]:
    """Greedily pack units into ~TARGET_TOKENS chunks with ~OVERLAP_TOKENS overlap."""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for unit in units:
        unit_tokens = estimate_tokens(unit)
        if current and current_tokens + unit_tokens > TARGET_TOKENS:
            chunks.append("\n".join(current).strip())
            # Build overlap tail from the end of the just-emitted chunk.
            overlap: list[str] = []
            otok = 0
            for u in reversed(current):
                if otok >= OVERLAP_TOKENS:
                    break
                overlap.insert(0, u)
                otok += estimate_tokens(u)
            current = overlap
            current_tokens = otok
        current.append(unit)
        current_tokens += unit_tokens

    if current:
        chunks.append("\n".join(current).strip())
    return [c for c in chunks if c]


# ── Public API ──────────────────────────────────────────────────────────────

_KEY_SAFE = re.compile(r"[^0-9A-Za-z_\-]")


def _chunk_id(source: str, page: int, local_index: int) -> str:
    """A stable, Azure-Search-legal document key (letters, digits, _, -)."""
    stem = _KEY_SAFE.sub("_", source)
    return f"{stem}-p{page}-{local_index}"


def chunk_document(doc: ExtractedDoc) -> list[Chunk]:
    """Turn an `ExtractedDoc` into page-aware, overlapped `Chunk`s."""
    chunks: list[Chunk] = []
    running = 0
    for page in doc.nonempty_pages:
        units = _split_into_units(page.text)
        for passage in _pack(units):
            if estimate_tokens(passage) < MIN_TOKENS:
                continue  # skip page-number/header noise
            chunks.append(
                Chunk(
                    id=_chunk_id(doc.source, page.number, running),
                    title=doc.title,
                    content=passage,
                    source=doc.source,
                    page=page.number,
                    chunk_index=running,
                )
            )
            running += 1
    return chunks
