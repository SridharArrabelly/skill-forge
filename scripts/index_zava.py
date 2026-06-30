"""index_zava.py — build the Zava Azure AI Search index end to end.

Pipeline: download docs -> extract (pluggable) -> page-aware chunk -> embed -> push.

Usage (from repo root, with the backend venv active or on PATH):

    python scripts/index_zava.py all                 # download + create-index + ingest
    python scripts/index_zava.py download            # just fetch the corpus to data/zava/
    python scripts/index_zava.py create-index        # (re)create the empty index
    python scripts/index_zava.py ingest              # extract + chunk + embed + push
    python scripts/index_zava.py chunk --dry-run     # chunk only, print counts (no Azure)

Flags:
    --extractor {content_understanding|doc_intelligence|pypdf|markdown}
                          override the extractor (default: $EXTRACTOR or content_understanding)
    --index NAME          override the index name (default: $SEARCH_INDEX_NAME)
    --no-recreate         keep an existing index instead of deleting + recreating it
    --dry-run             (chunk/ingest) stop before any Azure write; just report

Env is read from the repo .env if python-dotenv is installed; otherwise from the
process environment. Auth is keyless-first (run `az login`).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make `zava_ingest` importable whether run as `python scripts/index_zava.py` or `-m`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from zava_ingest import docs  # noqa: E402
from zava_ingest.chunking import Chunk, chunk_document  # noqa: E402
from zava_ingest.extractors import get_extractor, read_markdown  # noqa: E402


def _load_env() -> None:
    """Best-effort load of the repo .env so the script matches the backend's config."""
    try:
        from dotenv import load_dotenv
    except Exception:  # noqa: BLE001
        return
    root = Path(__file__).resolve().parent.parent
    for candidate in (root / ".env", root / "backend" / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)


def _extract_all(extractor_name: str) -> list:
    """Extract every corpus doc into `ExtractedDoc`s using the chosen extractor."""
    extractor = get_extractor(extractor_name)
    print(f"Extractor: {extractor.name}")
    extracted = []
    for d in docs.corpus():
        path = docs.data_dir() / d.filename
        if not path.exists():
            print(f"  ! missing {d.filename} — run `download` first")
            continue
        if d.is_markdown:
            ed = read_markdown(path, source=d.filename, title=d.title)
        else:
            ed = extractor.extract(path, source=d.filename, title=d.title)
        print(f"  {d.filename:46} pages={len(ed.nonempty_pages)}")
        extracted.append(ed)
    return extracted


def _chunk_all(extracted: list) -> list[Chunk]:
    chunks: list[Chunk] = []
    for ed in extracted:
        chunks.extend(chunk_document(ed))
    print(f"Chunks: {len(chunks)}")
    return chunks


def cmd_download(_: argparse.Namespace) -> int:
    paths = docs.download_all()
    print(f"Downloaded {len(paths)} docs to {docs.data_dir()}")
    return 0


def cmd_create_index(args: argparse.Namespace) -> int:
    from zava_ingest.search_index import create_index

    name = create_index(args.index, recreate=not args.no_recreate)
    print(f"Index ready: {name}")
    return 0


def cmd_chunk(args: argparse.Namespace) -> int:
    extracted = _extract_all(args.extractor)
    _chunk_all(extracted)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from zava_ingest.embeddings import Embedder
    from zava_ingest.search_index import push_chunks

    extracted = _extract_all(args.extractor)
    chunks = _chunk_all(extracted)
    if not chunks:
        print("No chunks produced — nothing to ingest.")
        return 1

    if args.dry_run:
        print("--dry-run: stopping before embeddings + push.")
        return 0

    embedder = Embedder()
    if not embedder.available():
        print("Embeddings not configured (AZURE_OPENAI_ENDPOINT / *_EMBED_DEPLOYMENT).")
        return 1
    print(f"Embedding {len(chunks)} chunks via {embedder.deployment} ...")
    vectors = embedder.embed([c.content for c in chunks])

    name = args.index or None
    print(f"Pushing {len(chunks)} docs ...")
    uploaded = push_chunks(chunks, vectors, name=name)
    print(f"Uploaded {uploaded} documents.")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    cmd_download(args)
    rc = cmd_create_index(args)
    if rc:
        return rc
    return cmd_ingest(args)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="index_zava", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    default_extractor = os.environ.get("EXTRACTOR", "content_understanding")

    for cmd in ("download", "create-index", "chunk", "ingest", "all"):
        sp = sub.add_parser(cmd)
        sp.add_argument("--extractor", default=default_extractor)
        sp.add_argument("--index", default=None)
        sp.add_argument("--no-recreate", action="store_true")
        sp.add_argument("--dry-run", action="store_true")

    return p


_DISPATCH = {
    "download": cmd_download,
    "create-index": cmd_create_index,
    "chunk": cmd_chunk,
    "ingest": cmd_ingest,
    "all": cmd_all,
}


def main(argv: list[str] | None = None) -> int:
    _load_env()
    args = _parser().parse_args(argv)
    return _DISPATCH[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
