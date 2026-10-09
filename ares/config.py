"""Runtime configuration loaded from environment variables.

Secrets are NEVER hardcoded. Copy ``.env.example`` to ``.env`` and add a key,
or leave it empty to run the clearly-labelled deterministic fallback agents
(the whole demo still works offline).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


#: Default Google Gemini model. ``gemini-3.5-flash-lite`` is the low-cost,
#: quota-friendly default (verified live on the team's key); override with
#: ``GEMINI_MODEL`` in ``.env`` to evaluate another model (removing the override
#: reverts automatically to this value, e.g. ``gemini-flash-lite-latest``).
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"


@dataclass
class Settings:
    provider: str = field(default_factory=lambda: os.getenv("ARES_LLM_PROVIDER", "auto").strip().lower())
    gemini_key: Optional[str] = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or None
    )
    openai_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY") or None)
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    temperature: float = field(default_factory=lambda: _get_float("ARES_LLM_TEMPERATURE", 0.4))
    timeout: int = field(default_factory=lambda: _get_int("ARES_LLM_TIMEOUT", 45))
    max_rounds: int = field(default_factory=lambda: _get_int("ARES_MAX_ROUNDS", 8))

    def resolved_provider(self) -> str:
        """Return the backend actually in use: 'gemini', 'openai', or 'fallback'."""
        if self.provider in ("fallback", "none", "rule", "rules"):
            return "fallback"
        if self.provider == "gemini":
            return "gemini" if self.gemini_key else "fallback"
        if self.provider == "openai":
            return "openai" if self.openai_key else "fallback"
        # auto
        if self.gemini_key:
            return "gemini"
        if self.openai_key:
            return "openai"
        return "fallback"

    def describe(self) -> str:
        provider = self.resolved_provider()
        if provider == "gemini":
            return f"Gemini ({self.gemini_model})"
        if provider == "openai":
            return f"OpenAI ({self.openai_model})"
        return "Rule-based fallback (offline, clearly labelled)"


settings = Settings()
