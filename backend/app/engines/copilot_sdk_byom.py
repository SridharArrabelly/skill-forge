"""Stage 2b engine: GitHub Copilot SDK with Bring-Your-Own-Model (BYOM).

This is the *same* engine as Stage 2 — the Copilot CLI runtime owns the agentic
loop, our skills are exposed as SDK `Tool`s — with exactly one thing changed: the
**model backend**. Instead of GitHub's hosted Copilot models, the runtime is
pointed at *your own Azure OpenAI deployment* via a `provider` config (BYOM). The
loop, the tool wiring, the streaming, and the allowlist are all inherited
unchanged from `CopilotSdkEngine`.

Why this engine exists: it's the clean A/B against Stage 2. Same Copilot loop,
only the model swaps — so any difference you observe is the model, not the
orchestration. (And the inference billing stays on your Azure subscription.)

Auth: keyless by default — the runtime gets a `get_bearer_token` callback that
mints an Azure AD token via `DefaultAzureCredential` (`az login`), the same
identity Stage 1 uses. Set `AZURE_OPENAI_API_KEY` to use key auth instead.

Caveat (see `byom.py`): the Copilot SDK encrypts prompts, so only o-series and
gpt-5 family Azure deployments decrypt them. `gpt-5.4-mini` works; `gpt-4o` will
not.
"""

from __future__ import annotations

from app.engines.byom import azure_byom_provider, make_bearer_token
from app.engines.copilot_sdk import CopilotSdkEngine


class CopilotSdkByomEngine(CopilotSdkEngine):
    id = "copilot_sdk_byom"
    label = "GitHub Copilot SDK (BYOM)"
    description = (
        "Same Copilot runtime loop as the Copilot SDK engine, but pointed at your "
        "own Azure OpenAI deployment via Bring-Your-Own-Model (provider config) "
        "instead of GitHub's hosted models. Keyless via DefaultAzureCredential."
    )

    def __init__(self, settings, toolset) -> None:
        super().__init__(settings, toolset)
        # BYOM runs on your Azure OpenAI *deployment*, not COPILOT_SDK_MODEL.
        self._model = settings.azure_openai_deployment or self._model
        # One token cache shared across turns (keyless path only).
        self._bearer = make_bearer_token()

    # ── Availability: needs the SDK *and* Azure OpenAI configured ─────────────

    @property
    def available(self) -> bool:
        return self._import_error is None and self.settings.azure_configured

    @property
    def unavailable_reason(self) -> str | None:
        base = super().unavailable_reason  # SDK-not-installed message, if any
        if base is not None:
            return base
        if not self.settings.azure_configured:
            return (
                "Azure OpenAI is not configured. Set AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_DEPLOYMENT in .env (auth is keyless via "
                "DefaultAzureCredential — run `az login`)."
            )
        return None

    # ── The one override: route the runtime at your Azure OpenAI ──────────────

    def _extra_session_kwargs(self) -> dict:
        return {"provider": azure_byom_provider(self.settings, bearer_token=self._bearer)}
