"""LLM adapter with automatic provider detection and safe failure.

Design goals:
    * Never crash the negotiation if the model or network is unavailable.
    * Support Google Gemini and OpenAI behind one tiny interface.
    * Always return either parsed JSON or ``None`` so the calling agent can use
      its clearly-labelled deterministic fallback policy.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Optional

from .config import settings


class LLMResult:
    def __init__(self, ok: bool, data: Optional[Dict[str, Any]] = None, error: str = "",
                 raw: str = "", kind: str = ""):
        self.ok = ok
        self.data = data
        self.error = error
        self.raw = raw
        self.kind = kind  # "rate_limit" | "timeout" | "connection" | "malformed" | ...


def _classify_error(exc: Exception) -> str:
    """Classify a provider exception so the client can fail over sensibly.

    The categories matter: a rate limit or a timeout will not be solved by
    calling again, so those fail over immediately. Only a malformed reply is
    worth one retry.
    """
    text = f"{type(exc).__name__}: {exc}".lower()

    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(exc, "code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None

    if code == 429 or any(t in text for t in (
        "429", "resource_exhausted", "rate limit", "ratelimit", "quota",
        "too many requests",
    )):
        return "rate_limit"
    if isinstance(exc, TimeoutError) or code == 408 or any(t in text for t in (
        "timeout", "timed out", "deadline exceeded",
    )):
        return "timeout"
    if any(t in text for t in (
        "connection", "connect", "network", "unavailable", "apiconnectionerror",
        "502", "503", "504",
    )):
        return "connection"
    return "error"


# Patterns used to redact likely secret material before an error is shown or
# persisted. Errors may contain URLs or SDK text but must never leak a key.
_SECRET_PATTERNS = (
    (re.compile(r"(?i)(\bapi[_-]?key\b[=:\s]*['\"]?)[A-Za-z0-9_\-.]{10,}"), r"\1<redacted>"),
    (re.compile(r"(?i)([\?&](?:key|api_key|token)=)[A-Za-z0-9_\-.]{8,}"), r"\1<redacted>"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{10,}\b"), "<redacted-key>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "<redacted-key>"),
)


def _sanitize(text: str, limit: int = 200) -> str:
    """Redact secret-shaped substrings and truncate before display or storage."""
    if not text:
        return ""
    cleaned = str(text)
    for pattern, repl in _SECRET_PATTERNS:
        cleaned = pattern.sub(repl, cleaned)
    return cleaned.strip()[:limit]


def _safe_exc_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {_sanitize(str(exc), 200)}"


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of a JSON object from model output."""
    if not text:
        return None
    cleaned = text.strip()

    # Strip markdown code fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    # Fall back to the first balanced { ... } block.
    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                snippet = cleaned[start : i + 1]
                try:
                    value = json.loads(snippet)
                    return value if isinstance(value, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


class LLMClient:
    """Thin synchronous wrapper around a chat model."""

    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or settings.resolved_provider()
        self._gemini_client = None
        self._openai_client = None
        self.last_error = ""
        self.last_error_kind = ""
        # True only once a REAL model request has actually returned usable JSON.
        # "Live" therefore means "a real request succeeded", not merely that a
        # key happens to be configured.
        self.live_verified = False
        # Bounded, sanitized history of every provider call this session. Used
        # for judge-facing telemetry (exports/llm_operations_log.json). Never
        # contains a key: errors are redacted and truncated on the way in.
        self.call_log: list = []

    @property
    def available(self) -> bool:
        return self.provider in ("gemini", "openai")

    def describe(self) -> str:
        if self.provider == settings.resolved_provider():
            return settings.describe()
        if self.provider == "fallback":
            return "Rule-based fallback (offline, clearly labelled)"
        return self.provider

    # -- provider setup ---------------------------------------------------
    def _get_gemini(self):
        if self._gemini_client is None:
            from google import genai  # lazy import
            from google.genai import types as genai_types  # lazy import

            # Enforce the configured timeout (seconds) on Gemini too, so a
            # stalled request fails fast and the agent falls back instead of
            # hanging the negotiation. HttpOptions.timeout is in milliseconds.
            self._gemini_client = genai.Client(
                api_key=settings.gemini_key,
                http_options=genai_types.HttpOptions(timeout=settings.timeout * 1000),
            )
        return self._gemini_client

    def _get_openai(self):
        if self._openai_client is None:
            from openai import OpenAI  # lazy import

            self._openai_client = OpenAI(api_key=settings.openai_key, timeout=settings.timeout)
        return self._openai_client

    # -- public API -------------------------------------------------------
    def complete_json(
        self,
        system: str,
        user: str,
        retries: int = 1,
    ) -> LLMResult:
        """Ask the model for a JSON object. Returns LLMResult(ok=False) on any failure.

        Error policy (the negotiation must never crash):
          * rate limit / quota (HTTP 429) -> fail over immediately, NO retry.
            Retrying cannot refill a quota, so we never pretend that it does.
          * timeout / connection error -> fail over immediately, NO retry.
          * malformed (non-JSON) output -> one retry, then fail over.
        The caller then applies its clearly-labelled deterministic fallback.
        """
        self.last_error = ""
        self.last_error_kind = ""
        if not self.available:
            self.last_error = "no provider configured"
            self.last_error_kind = "no_provider"
            result = LLMResult(False, error=self.last_error, kind="no_provider")
            return self._record(result, time.time())

        started = time.time()
        for attempt in range(retries + 1):
            try:
                raw = self._call(system, user)
            except Exception as exc:  # noqa: BLE001 - must never propagate
                kind = _classify_error(exc)
                self.last_error = _safe_exc_text(exc)
                self.last_error_kind = kind
                result = LLMResult(False, error=self.last_error, kind=kind)
                if kind in ("rate_limit", "timeout", "connection"):
                    return self._record(result, started)
                if attempt < retries:
                    continue
                return self._record(result, started)

            data = _extract_json(raw)
            if data is not None:
                self.live_verified = True
                result = LLMResult(True, data=data, raw=raw)
                return self._record(result, started)

            self.last_error = "model returned non-JSON output"
            self.last_error_kind = "malformed"
            if attempt < retries:
                continue
            result = LLMResult(False, error=self.last_error, kind="malformed")
            return self._record(result, started)

        result = LLMResult(False, error=self.last_error, kind=self.last_error_kind)
        return self._record(result, started)

    def _record(self, result: LLMResult, started: Optional[float] = None) -> LLMResult:
        """Append one sanitized call outcome to ``call_log`` (bounded)."""
        if self.provider == "gemini":
            model = settings.gemini_model
        elif self.provider == "openai":
            model = settings.openai_model
        else:
            model = "n/a"
        elapsed = round(time.time() - started, 3) if started else 0.0
        self.call_log.append({
            "ts": time.time(),
            "provider": self.provider,
            "model": model,
            "ok": result.ok,
            "kind": result.kind,
            "elapsed": elapsed,
            "error": _sanitize(result.error, 200) if not result.ok else "",
        })
        if len(self.call_log) > 500:
            self.call_log = self.call_log[-500:]
        return result

    @property
    def last_error_safe(self) -> str:
        """Human-safe view of the last failure: redacted and truncated."""
        return _sanitize(self.last_error, 200)

    def _call(self, system: str, user: str) -> str:
        if self.provider == "gemini":
            return self._call_gemini(system, user)
        if self.provider == "openai":
            return self._call_openai(system, user)
        raise RuntimeError("no provider configured")

    def _call_gemini(self, system: str, user: str) -> str:
        from google.genai import types

        client = self._get_gemini()
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=settings.temperature,
            response_mime_type="application/json",
        )
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=user,
            config=config,
        )
        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("empty Gemini response")
        return text

    def _call_openai(self, system: str, user: str) -> str:
        client = self._get_openai()
        response = client.chat.completions.create(
            model=settings.openai_model,
            temperature=settings.temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = response.choices[0].message.content
        if not text:
            raise RuntimeError("empty OpenAI response")
        return text
