"""Shared pytest configuration.

Two jobs:

1. Ensure pytest can import the ``ares`` package from the repository root.
2. Keep the test-suite hermetic: force the deterministic, offline rule-based
   provider for the whole pytest session. Without this, once a real
   ``GEMINI_API_KEY``/``OPENAI_API_KEY`` is present in ``.env`` the dashboard
   tests would make live (slow, flaky, billable) network calls instead of being
   deterministic. Live providers are exercised separately and on purpose, never
   by the automated suite.

   ``load_dotenv()`` in ``ares.config`` does not override variables that already
   exist in the environment, so setting this before ``ares`` is imported pins
   the provider to ``fallback`` for the tests.
"""

import os

os.environ.setdefault("ARES_LLM_PROVIDER", "fallback")
os.environ["ARES_LLM_PROVIDER"] = "fallback"
