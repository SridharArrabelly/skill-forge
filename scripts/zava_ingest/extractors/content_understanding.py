"""Azure AI Content Understanding extractor (default).

Calls the GA ``prebuilt-layout`` analyzer — content + layout (paragraphs, tables,
figures, sections) returned as markdown — and splits it into pages for citations.

We use ``prebuilt-layout`` rather than ``prebuilt-documentSearch`` on purpose: layout
extraction needs **no language/embedding model** (so it works keyless with zero extra
setup), whereas documentSearch's generative enrichment (figure descriptions, document
summaries) requires registering a completion-model deployment on the resource defaults.
For benefits prose + comparison tables, layout markdown is exactly what we want.
Override with ``CONTENT_UNDERSTANDING_ANALYZER=prebuilt-documentSearch`` if you've
configured a completion model and want the richer enrichment.

REST shape (api-version 2025-11-01):

    POST {endpoint}/contentunderstanding/analyzers/prebuilt-layout:analyzeBinary
         ?api-version=2025-11-01
    Content-Type: <file media type, e.g. application/pdf>; body = raw file bytes.
    → 202 with an `Operation-Location` header; poll it until status == "Succeeded".
    (The sibling `:analyze` endpoint is JSON/URL-only — local bytes need :analyzeBinary.)

Auth is keyless-first: leave ``CONTENT_UNDERSTANDING_KEY`` blank to use
``DefaultAzureCredential`` (``az login`` / managed identity) against the Cognitive
Services scope. Set the key to use ``Ocp-Apim-Subscription-Key`` instead.

Env:
    CONTENT_UNDERSTANDING_ENDPOINT      e.g. https://<name>.services.ai.azure.com/
    CONTENT_UNDERSTANDING_API_VERSION   default "2025-11-01"
    CONTENT_UNDERSTANDING_ANALYZER      default "prebuilt-layout"
    CONTENT_UNDERSTANDING_KEY           optional; blank → DefaultAzureCredential
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from . import ExtractedDoc, Page
from ._pagebreak import split_markdown_into_pages

_SCOPE = "https://cognitiveservices.azure.com/.default"
_DEFAULT_API_VERSION = "2025-11-01"
_DEFAULT_ANALYZER = "prebuilt-layout"

# Content Understanding wants the real media type on :analyzeBinary uploads.
_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".html": "text/html",
    ".txt": "text/plain",
}


def _content_type(path: Path) -> str:
    return _MIME_BY_SUFFIX.get(path.suffix.lower(), "application/octet-stream")


class ContentUnderstandingExtractor:
    name = "content_understanding"

    def __init__(self) -> None:
        self._endpoint = os.environ.get("CONTENT_UNDERSTANDING_ENDPOINT", "").strip().rstrip("/")
        self._api_version = os.environ.get(
            "CONTENT_UNDERSTANDING_API_VERSION", _DEFAULT_API_VERSION
        ).strip()
        self._analyzer = os.environ.get(
            "CONTENT_UNDERSTANDING_ANALYZER", _DEFAULT_ANALYZER
        ).strip()
        self._key = os.environ.get("CONTENT_UNDERSTANDING_KEY", "").strip()
        self._credential = None  # lazy DefaultAzureCredential

    def available(self) -> bool:
        return bool(self._endpoint)

    # ── auth ────────────────────────────────────────────────────────────────
    def _auth_headers(self) -> dict[str, str]:
        if self._key:
            return {"Ocp-Apim-Subscription-Key": self._key}
        if self._credential is None:
            from azure.identity import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
        token = self._credential.get_token(_SCOPE).token
        return {"Authorization": f"Bearer {token}"}

    # ── extract ──────────────────────────────────────────────────────────────
    def extract(self, path: Path, *, source: str, title: str) -> ExtractedDoc:
        import httpx

        if not self.available():
            raise RuntimeError(
                "Content Understanding is not configured. Set "
                "CONTENT_UNDERSTANDING_ENDPOINT (or choose EXTRACTOR=pypdf)."
            )

        # Local bytes go to :analyzeBinary (the :analyze endpoint is JSON/URL-only).
        analyze_url = (
            f"{self._endpoint}/contentunderstanding/analyzers/"
            f"{self._analyzer}:analyzeBinary?api-version={self._api_version}"
        )
        headers = {**self._auth_headers(), "Content-Type": _content_type(path)}
        body = path.read_bytes()

        with httpx.Client(timeout=120.0) as client:
            resp = client.post(analyze_url, headers=headers, content=body)
            if resp.status_code not in (200, 201, 202):
                raise RuntimeError(
                    f"Content Understanding analyze failed ({resp.status_code}): {resp.text[:500]}"
                )

            # Async op: poll the Operation-Location header until terminal state.
            op_url = resp.headers.get("operation-location") or resp.headers.get("Operation-Location")
            result = resp.json() if not op_url else self._poll(client, op_url)

        markdown = _markdown_from_result(result)
        pages = split_markdown_into_pages(markdown)
        if not pages:
            pages = [Page(number=1, text=markdown.strip())] if markdown.strip() else []
        return ExtractedDoc(source=source, title=title, pages=pages)

    def _poll(self, client, op_url: str, *, max_wait: float = 180.0) -> dict:
        poll_headers = self._auth_headers()
        deadline = time.monotonic() + max_wait
        delay = 1.0
        while True:
            r = client.get(op_url, headers=poll_headers)
            r.raise_for_status()
            data = r.json()
            status = str(data.get("status", "")).lower()
            if status in ("succeeded", "completed"):
                return data
            if status in ("failed", "canceled", "cancelled"):
                raise RuntimeError(f"Content Understanding analyze {status}: {str(data)[:500]}")
            if time.monotonic() > deadline:
                raise TimeoutError("Content Understanding analyze timed out.")
            time.sleep(delay)
            delay = min(delay * 1.5, 5.0)
            poll_headers = self._auth_headers()  # refresh token if needed


def _markdown_from_result(data: dict) -> str:
    """Pull markdown out of a Content Understanding result, defensively.

    The GA result nests markdown under ``result.contents[*].markdown``; older/preview
    shapes used ``result.markdown``. We gather every markdown string we find (joined
    with page breaks so multi-content docs keep page structure).
    """
    result = data.get("result", data)

    contents = result.get("contents")
    if isinstance(contents, list):
        parts = [c.get("markdown", "") for c in contents if isinstance(c, dict) and c.get("markdown")]
        if parts:
            return "\n\n<!-- PageBreak -->\n\n".join(parts)

    for key in ("markdown", "markdownContent", "content"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val

    return ""
