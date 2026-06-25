"""Configuration for skill-forge.

All settings come from environment variables (optionally a local .env file).
Keeping this tiny and explicit is deliberate — you should be able to read the
whole config surface in one screen.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = two levels up from this file (backend/app/config.py -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime settings.

    Loaded from the process environment and, if present, a `.env` at the repo
    root. Unknown env vars are ignored so the same .env can hold future keys.
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Azure OpenAI (the brain behind the loop) ────────────────────────────
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-10-21"

    # ── Skills ──────────────────────────────────────────────────────────────
    # Directory (relative to repo root, or absolute) that holds skill folders.
    skills_dir: str = "skills"

    # Safety rail: max Reason→Act→Observe iterations before we force a stop.
    max_agent_iterations: int = 6

    @property
    def skills_path(self) -> Path:
        """Absolute path to the skills directory."""
        p = Path(self.skills_dir)
        return p if p.is_absolute() else (REPO_ROOT / p)

    @property
    def azure_configured(self) -> bool:
        """True when enough Azure OpenAI settings exist to make a call."""
        return bool(
            self.azure_openai_endpoint
            and self.azure_openai_api_key
            and self.azure_openai_deployment
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
