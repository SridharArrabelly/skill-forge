"""The Zava corpus: 7 fictitious HR/benefits docs to index.

Sourced from `SridharArrabelly/azure-search-openai-demo` `data/` (we deliberately
ignore the `Json_Examples` and `Multimodal_Examples` folders). URLs are pinned to a
commit SHA so the corpus is reproducible — a re-run indexes the exact same bytes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Pinned commit so the corpus never shifts under us.
_REPO = "SridharArrabelly/azure-search-openai-demo"
_SHA = "f564433db42fbc9334bcb7522a08801b508577b5"
_RAW_BASE = f"https://raw.githubusercontent.com/{_REPO}/{_SHA}/data"

# Files to index (ignoring the Json_Examples / Multimodal_Examples folders).
_FILENAMES = [
    "Zava_Company_Overview.md",
    "Benefit_Options.pdf",
    "Northwind_Health_Plus_Benefits_Details.pdf",
    "Northwind_Standard_Benefits_Details.pdf",
    "PerksPlus.pdf",
    "employee_handbook.pdf",
    "role_library.pdf",
]


@dataclass(frozen=True)
class SourceDoc:
    """One source file: its filename, a human title, and where to fetch it."""

    filename: str
    url: str

    @property
    def title(self) -> str:
        """A readable title from the filename, e.g. 'Northwind Health Plus Benefits Details'."""
        stem = Path(self.filename).stem
        return stem.replace("_", " ").strip()

    @property
    def is_markdown(self) -> bool:
        return self.filename.lower().endswith(".md")


def corpus() -> list[SourceDoc]:
    """The full Zava corpus as `SourceDoc`s."""
    return [SourceDoc(filename=name, url=f"{_RAW_BASE}/{name}") for name in _FILENAMES]


def data_dir() -> Path:
    """Local corpus folder (committed to the repo). Override with ZAVA_DATA_DIR."""
    override = os.environ.get("ZAVA_DATA_DIR", "").strip()
    base = Path(override) if override else Path(__file__).resolve().parents[2] / "data" / "zava"
    base.mkdir(parents=True, exist_ok=True)
    return base


def download(doc: SourceDoc, *, force: bool = False) -> Path:
    """Download one doc into the cache (skipping if already present) and return its path."""
    import httpx

    dest = data_dir() / doc.filename
    if dest.exists() and dest.stat().st_size > 0 and not force:
        return dest

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        resp = client.get(doc.url)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
    return dest


def download_all(*, force: bool = False) -> list[tuple[SourceDoc, Path]]:
    """Download the whole corpus; return (doc, local_path) pairs."""
    return [(doc, download(doc, force=force)) for doc in corpus()]
