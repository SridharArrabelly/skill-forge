# ZAVA index ingestion (`scripts/`)

Builds the `rag-search` knowledge base: the **ZAVA** corpus (fictitious HR / benefits
docs in `data/zava/`) → an Azure AI Search index (`zava-benefits-index`) with hybrid +
semantic retrieval.

```
download → extract (pluggable) → page-aware chunk → embed → push
```

## Layout

```
scripts/
  index_zava.py            # orchestrator CLI (download | create-index | chunk | ingest | all)
  zava_ingest/
    docs.py                # the 7 source docs (pinned to a commit SHA) + downloader
    chunking.py            # page-aware ~500-token / 10%-overlap chunker (tables kept whole)
    embeddings.py          # Azure OpenAI text-embedding-3-small client (keyless-first)
    search_index.py        # create index (vector HNSW + zava-semantic) + push docs
    extractors/            # pluggable PDF → markdown
      content_understanding.py   # DEFAULT — Azure AI Content Understanding (prebuilt-layout)
      doc_intelligence.py        # alternate — Azure AI Document Intelligence (prebuilt-layout)
      pypdf_extract.py           # zero-setup local fallback
```

## Prerequisites

* Python deps: `pip install -r backend/requirements.txt` (the script reuses them).
* **Keyless auth**: `az login`. Your identity needs:
  * `Search Index Data Contributor` + `Search Service Contributor` on the Search service,
  * `Cognitive Services User` on the Foundry/AI-Services resource (Content Understanding),
  * access to the Azure OpenAI embeddings deployment.
* Config in the repo `.env` (see `.env.example`): `AZURE_SEARCH_ENDPOINT`,
  `SEARCH_INDEX_NAME`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_EMBED_DEPLOYMENT`,
  `CONTENT_UNDERSTANDING_ENDPOINT`, `EXTRACTOR`.

## Usage

```bash
# Everything: fetch corpus, (re)create the index, extract+chunk+embed+push.
python scripts/index_zava.py all

# Or step by step:
python scripts/index_zava.py download
python scripts/index_zava.py create-index
python scripts/index_zava.py ingest

# Inspect chunking without touching Azure (no auth needed):
python scripts/index_zava.py chunk --extractor pypdf
python scripts/index_zava.py ingest --extractor pypdf --dry-run
```

### Flags

| Flag | Meaning |
|------|---------|
| `--extractor {content_understanding,doc_intelligence,pypdf,markdown}` | override the extractor (default `$EXTRACTOR`) |
| `--index NAME` | override the index name (default `$SEARCH_INDEX_NAME`) |
| `--no-recreate` | keep an existing index instead of delete+recreate |
| `--dry-run` | (chunk/ingest) stop before any Azure write |

## Why these choices

* **Pluggable extractor** — Content Understanding is the newest GA service and gives clean,
  layout-aware markdown, but you can swap to Document Intelligence or local `pypdf` with one
  flag, so the pipeline runs even with no document service configured.
* **Page-aware chunking** — chunks never cross a page boundary, so every retrieved passage
  carries one true page number → clean, verifiable citations.
* **Hybrid + semantic** — keyword recall + vector similarity + the semantic reranker is the
  combination that performs best on benefits prose and the comparison tables.

## Index schema (`zava-benefits-index`)

| field | type | role |
|-------|------|------|
| `id` | String (key) | Search-legal doc key (`<source>-p<page>-<n>`) |
| `title` | searchable | document title |
| `content` | searchable | passage text |
| `source` | filterable | original filename |
| `page` | filterable/sortable | 1-based page number |
| `chunk_index` | filterable/sortable | global chunk ordinal |
| `content_vector` | vector(1536) | `text-embedding-3-small`, HNSW |

Semantic config: `zava-semantic`. The `rag-search` skill reads from this index.
