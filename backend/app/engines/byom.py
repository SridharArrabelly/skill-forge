"""Shared Azure "Bring Your Own Model" (BYOM) provider config.

Both BYOM engines — Stage 2b (`copilot_sdk_byom`) and the reworked Stage 3
(`agent_framework`, now backed by the Copilot SDK) — route the Copilot runtime at
*your* Azure OpenAI deployment instead of GitHub's hosted models. The runtime
takes a `provider` config (a `copilot.session.ProviderConfig` TypedDict); this
module builds that dict once so the two engines share exactly one recipe.

Why keyless works the same as everywhere else: we hand the runtime a
`get_bearer_token` callback that mints an Azure AD token via
`DefaultAzureCredential` (the same `az login` identity Stage 1 uses), scoped to
Cognitive Services. If a real key is set we pass it through as `api_key` instead.

One sharp edge worth knowing (the reason this isn't "any Azure deployment"):
**the Copilot SDK encrypts prompts before sending them.** Only model families that
can decrypt that format work end-to-end — the o-series and the gpt-5 family. A
gpt-4o deployment will fail with "Encrypted content is not supported", so we expose
a small support check + a friendlier error rewrite.
"""

from __future__ import annotations

import threading
import time
from typing import Any

# Token scope for Azure OpenAI / Cognitive Services data-plane calls.
_AOAI_SCOPE = "https://cognitiveservices.azure.com/.default"

# Azure OpenAI *Responses* API version the Copilot runtime speaks for BYOM.
# (The repo's AZURE_OPENAI_API_VERSION — 2024-10-21 — is the older
# chat-completions version Stage 1 uses; the Responses wire needs a newer one.)
_BYOM_API_VERSION = "2025-04-01-preview"

# Refresh the cached AAD token this long before it actually expires.
_TOKEN_REFRESH_BUFFER_S = 5 * 60

# Model families that support the Copilot SDK's encrypted-content format.
SUPPORTED_MODEL_PREFIXES = ("o3", "o4-mini", "gpt-5", "codex-mini")


def model_supports_encrypted_content(model_name: str) -> bool:
    """True if `model_name` is an o-series or gpt-5 family model.

    Matches exact name or `<prefix>-...` / `<prefix>....` (e.g. "gpt-5",
    "gpt-5-mini", "gpt-5.4-mini" all match the "gpt-5" prefix).
    """
    lower = (model_name or "").lower()
    return any(
        lower == p or lower.startswith(p + "-") or lower.startswith(p + ".")
        for p in SUPPORTED_MODEL_PREFIXES
    )


def unsupported_model_message(model_name: str) -> str:
    return (
        f'Model "{model_name}" may not support the Copilot SDK\'s encrypted-content '
        "format. Only o-series (o3, o3-mini, o4-mini) and gpt-5 family deployments "
        "work via BYOM. Change AZURE_OPENAI_DEPLOYMENT to a supported model "
        "(e.g. gpt-5.4-mini or o4-mini)."
    )


def enhance_byom_error(model_name: str, err: Exception) -> str:
    """Turn the runtime's terse encrypted-content error into a helpful one."""
    msg = str(err)
    if "Encrypted content is not supported" in msg:
        return (
            f'Model "{model_name}" does not support encrypted content. The Copilot '
            "SDK encrypts prompts, so only o-series and gpt-5 family Azure OpenAI "
            "deployments work via BYOM. " + unsupported_model_message(model_name)
        )
    return msg


class _TokenProvider:
    """Caches an AAD bearer token and refreshes it shortly before expiry.

    Built lazily (and only when keyless) so importing this module never touches
    azure.identity. One instance is reused per engine via `make_bearer_token`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._credential = None
        self._token: str | None = None
        self._expires_on: float = 0.0

    def __call__(self, *_args: Any) -> str:
        with self._lock:
            now = time.time()
            if self._token and now < self._expires_on - _TOKEN_REFRESH_BUFFER_S:
                return self._token
            if self._credential is None:
                from azure.identity import DefaultAzureCredential

                self._credential = DefaultAzureCredential()
            result = self._credential.get_token(_AOAI_SCOPE)
            self._token = result.token
            self._expires_on = float(result.expires_on)
            return self._token


def make_bearer_token():
    """Return a cached `get_bearer_token` callable for keyless BYOM auth."""
    return _TokenProvider()


def azure_byom_provider(settings, *, bearer_token=None) -> dict:
    """Build the Copilot runtime `provider` dict pointing at your Azure OpenAI.

    Keyless by default (a `get_bearer_token` callback via DefaultAzureCredential);
    if `AZURE_OPENAI_API_KEY` is set we pass it as `api_key` instead. Pass a shared
    `bearer_token` callable to reuse one token cache across turns.
    """
    provider: dict[str, Any] = {
        "type": "azure",
        "wire_api": "responses",
        "base_url": settings.azure_openai_endpoint.rstrip("/"),
        "azure": {"api_version": _BYOM_API_VERSION},
    }
    if settings.use_entra_auth:
        provider["get_bearer_token"] = bearer_token or make_bearer_token()
    else:
        provider["api_key"] = settings.azure_openai_api_key
    return provider
