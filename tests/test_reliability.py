"""Reliability tests: LLM failure/fallback and export correctness.

These cover two requirements that the rest of the suite only touched
indirectly:

* the language-model adapter must never crash the negotiation — on any error
  it must return a failure and let the agent use its clearly-labelled
  deterministic fallback policy;
* the JSON/CSV exports must be real records derived from the live state,
  not decorative files.
"""

import csv
import json
import os

from ares import validator
from ares.agents import AresAgent, NegotiationContext
from ares.config import DEFAULT_GEMINI_MODEL, Settings, settings
from ares.engine import AresEngine
from ares.llm import LLMClient, LLMResult, _extract_json
from ares.models import DEPARTMENT_ROLES, Role, engine_badge
from ares.packages import DEFAULT_RESOURCES, RESOURCE_ORDER


def _offline_engine() -> AresEngine:
    return AresEngine(llm=LLMClient(provider="fallback"))


# --- LLM adapter: parsing and safe failure ----------------------------------

def test_extract_json_accepts_fenced_prose_and_rejects_non_objects():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('Sure! Here it is: {"a": 2, "b": {"c": 3}} -- done') == {
        "a": 2,
        "b": {"c": 3},
    }
    assert _extract_json("[1, 2, 3]") is None  # a JSON array is not a valid reply
    assert _extract_json("I think we should share the power.") is None
    assert _extract_json("") is None


def test_llm_client_is_unavailable_without_a_provider():
    client = LLMClient(provider="fallback")
    assert client.available is False
    result = client.complete_json("system", "user")
    assert result.ok is False
    assert result.data is None


def test_llm_client_swallows_provider_exceptions(monkeypatch):
    client = LLMClient(provider="openai")
    assert client.available is True

    def boom(system: str, user: str) -> str:
        raise TimeoutError("network timeout")

    monkeypatch.setattr(client, "_call", boom)
    result = client.complete_json("system", "user")
    assert result.ok is False
    assert result.data is None
    assert "TimeoutError" in client.last_error


def test_llm_client_rejects_non_json_output(monkeypatch):
    client = LLMClient(provider="openai")
    monkeypatch.setattr(client, "_call", lambda system, user: "Let us all share.")
    result = client.complete_json("system", "user")
    assert result.ok is False
    assert result.data is None


def test_live_verified_only_true_after_a_real_success(monkeypatch):
    # "Live" must mean a real request actually succeeded, never merely that a
    # provider/key is configured. A failure must not flip the flag.
    client = LLMClient(provider="openai")
    assert client.live_verified is False

    monkeypatch.setattr(client, "_call", lambda system, user: "not json at all")
    assert client.complete_json("system", "user").ok is False
    assert client.live_verified is False

    monkeypatch.setattr(client, "_call", lambda system, user: '{"ok": true}')
    assert client.complete_json("system", "user").ok is True
    assert client.live_verified is True


def test_offline_client_never_claims_live_verified():
    client = LLMClient(provider="fallback")
    assert client.available is False
    assert client.complete_json("system", "user").ok is False
    assert client.live_verified is False


def test_engine_badge_never_calls_fallback_live():
    assert engine_badge("llm").endswith("LIVE AI")
    assert engine_badge("fallback").endswith("FALLBACK")
    assert engine_badge("system").endswith("SYSTEM")
    assert engine_badge("anything-else").endswith("SYSTEM")


def test_rate_limit_fails_over_immediately_without_retrying(monkeypatch):
    # A 429/quota error cannot be fixed by calling again, so the client must
    # NOT make repeated calls and must report the true reason.
    client = LLMClient(provider="gemini")
    calls = {"n": 0}

    class _RateLimited(Exception):
        status_code = 429

    def boom(system: str, user: str) -> str:
        calls["n"] += 1
        raise _RateLimited("429 RESOURCE_EXHAUSTED: quota exceeded")

    monkeypatch.setattr(client, "_call", boom)
    result = client.complete_json("system", "user")

    assert result.ok is False
    assert calls["n"] == 1, "rate limits must not trigger repeated API calls"
    assert result.kind == "rate_limit"
    assert client.last_error_kind == "rate_limit"


def test_timeout_fails_over_immediately_without_retrying(monkeypatch):
    client = LLMClient(provider="gemini")
    calls = {"n": 0}

    def boom(system: str, user: str) -> str:
        calls["n"] += 1
        raise TimeoutError("request timed out")

    monkeypatch.setattr(client, "_call", boom)
    result = client.complete_json("system", "user")

    assert result.ok is False
    assert calls["n"] == 1, "timeouts must not trigger repeated API calls"
    assert result.kind == "timeout"


def test_malformed_output_is_retried_once_then_succeeds(monkeypatch):
    client = LLMClient(provider="openai")
    replies = iter(["not json at all", '{"decision": "ACCEPT"}'])

    monkeypatch.setattr(client, "_call", lambda system, user: next(replies))
    result = client.complete_json("system", "user")

    assert result.ok is True
    assert result.data == {"decision": "ACCEPT"}


def test_malformed_output_fails_over_after_one_retry(monkeypatch):
    client = LLMClient(provider="openai")
    calls = {"n": 0}

    def prose(system: str, user: str) -> str:
        calls["n"] += 1
        return "I think we should share."

    monkeypatch.setattr(client, "_call", prose)
    result = client.complete_json("system", "user")

    assert result.ok is False
    assert result.kind == "malformed"
    assert calls["n"] == 2, "malformed output gets exactly one retry"


def test_agents_fall_back_cleanly_when_the_model_returns_nothing(monkeypatch):
    # A model is "configured", but every call fails: the whole negotiation must
    # still complete, and every agent message must be honestly labelled.
    client = LLMClient(provider="openai")
    monkeypatch.setattr(
        client,
        "complete_json",
        lambda system, user, retries=2: LLMResult(False, error="simulated outage"),
    )

    engine = AresEngine(llm=client)
    outcome = engine.start()

    assert outcome.status == "APPROVED", outcome.detail
    agent_messages = [m for m in engine.transcript if m.sender in DEPARTMENT_ROLES]
    assert agent_messages, "agents must still exchange messages"
    assert all(m.engine == "fallback" for m in agent_messages)
    assert all(m.engine != "llm" for m in engine.transcript), "nothing may be labelled live"


def test_rate_limited_engine_still_completes_with_labelled_fallback(monkeypatch):
    # Live mode, but the provider is rate-limited on every call. The negotiation
    # must still finish and each agent message must be tagged fallback.
    client = LLMClient(provider="gemini")
    monkeypatch.setattr(
        client,
        "complete_json",
        lambda system, user, retries=1: LLMResult(
            False, error="429 RESOURCE_EXHAUSTED: quota exceeded", kind="rate_limit"
        ),
    )

    engine = AresEngine(llm=client)
    outcome = engine.start()

    assert outcome.status == "APPROVED", outcome.detail
    agent_messages = [m for m in engine.transcript if m.sender in DEPARTMENT_ROLES]
    assert agent_messages
    assert all(m.engine == "fallback" for m in agent_messages)
    assert all(v.engine != "llm" for v in engine.commander.all_votes)


def test_successful_model_output_is_labelled_llm(monkeypatch):
    # The reverse guarantee: real model output IS labelled "llm".
    client = LLMClient(provider="openai")
    monkeypatch.setattr(
        client,
        "complete_json",
        lambda system, user, retries=2: LLMResult(
            True, data={"mode_id": "L1", "justification": "model says so"}
        ),
    )
    agent = AresAgent(Role.LIFE_SUPPORT, llm=client, transcript=[])
    ctx = NegotiationContext(
        scenario_id="TEST", resources=DEFAULT_RESOURCES, risk_limit=24,
        max_sacrifices=1, round=1, plan_version=1, phase="propose",
    )
    message = agent.propose(ctx)
    assert message.engine == "llm"


def test_llm_cannot_vote_to_approve_an_unsafe_plan(monkeypatch):
    # The deterministic validator is independent of the model. Even if the model
    # enthusiastically votes ACCEPT, an agent can never ACCEPT a plan the
    # validator rejected: the safety contract overrides it (and the overridden
    # vote is labelled "fallback", never "llm").
    client = LLMClient(provider="openai")
    monkeypatch.setattr(
        client,
        "complete_json",
        lambda system, user, retries=1: LLMResult(
            True, data={"decision": "ACCEPT", "reason": "the model accepts anything"}
        ),
    )
    selections = {
        "Life Support": "L1", "Medical": "M1",
        "Food Production": "F1", "Engineering": "E1",
    }
    empty_pool = {k: 0 for k in RESOURCE_ORDER}
    verdict = validator.validate_plan(selections, empty_pool, 24, 1, plan_version=1)
    assert verdict.passed is False

    agent = AresAgent(Role.LIFE_SUPPORT, llm=client, transcript=[])
    ctx = NegotiationContext(
        scenario_id="SAFETY", resources=empty_pool, risk_limit=24, max_sacrifices=1,
        round=4, plan_version=1, phase="vote",
        plan_selections=selections, validation=verdict,
    )
    vote = agent.vote(ctx)
    assert vote.decision.value == "REJECT"
    assert vote.engine == "fallback"


def test_gemini_client_is_created_with_the_configured_timeout(monkeypatch):
    # The guide requires timeout handling. Both providers must apply the same
    # configured timeout so a stalled model degrades to the labelled fallback.
    captured = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import google.genai as genai_module

    monkeypatch.setattr(genai_module, "Client", _FakeClient)

    client = LLMClient(provider="gemini")
    client._get_gemini()

    http_options = captured.get("http_options")
    assert http_options is not None
    from ares.config import settings

    assert http_options.timeout == settings.timeout * 1000


def test_gemini_model_defaults_to_verified_stable_model(monkeypatch):
    # The default model is the low-cost quota-friendly one we verified live, and
    # it is not hardcoded in the adapter: the dataclass default comes from a
    # named constant so it can be audited and overridden.
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert Settings().gemini_model == DEFAULT_GEMINI_MODEL
    assert DEFAULT_GEMINI_MODEL == "gemini-3.5-flash-lite"


def test_gemini_model_is_reading_from_environment(monkeypatch):
    # GEMINI_MODEL must drive the configured model, so switching models (and
    # reverting) is a one-line .env change with no code edit.
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    assert Settings().gemini_model == "gemini-3.5-flash-lite"

    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert Settings().gemini_model == DEFAULT_GEMINI_MODEL


def test_gemini_request_uses_the_configured_model(monkeypatch):
    # Whatever the configuration resolves to must be the model that is actually
    # sent in the request. No network: the provider call is faked.
    captured = {}

    class _FakeResp:
        text = '{"ok": true}'

    class _FakeModels:
        def generate_content(self, model, contents, config):
            captured["model"] = model
            return _FakeResp()

    class _FakeGemini:
        models = _FakeModels()

    client = LLMClient(provider="gemini")
    monkeypatch.setattr(client, "_get_gemini", lambda: _FakeGemini())
    result = client.complete_json("system", "user")

    assert result.ok is True
    assert captured["model"] == settings.gemini_model
    assert captured["model"], "the model selection must never be empty"


# --- Export correctness ------------------------------------------------------

def test_exports_exist_and_reflect_live_state(tmp_path):
    engine = _offline_engine()
    engine.start()
    plan = engine.final_plan
    assert plan is not None and plan.status.value == "APPROVED"

    engine.export(str(tmp_path))

    for name in (
        "final_allocation.json",
        "council_transcript.json",
        "council_transcript.csv",
        "plans_history.json",
        "crisis_handling_log.json",
    ):
        assert os.path.exists(tmp_path / name), f"missing export: {name}"

    allocation = json.loads((tmp_path / "final_allocation.json").read_text(encoding="utf-8"))
    # The exported allocation must match the live approved plan exactly.
    assert allocation["allocation"]["totals"] == plan.totals
    assert allocation["allocation"]["risk"] == plan.risk
    assert allocation["allocation"]["plan_version"] == plan.version
    assert allocation["allocation"]["status"] == "APPROVED"
    assert allocation["allocation"]["selections"] == plan.selections

    # The output_schema view stays consistent with the same plan.
    record = allocation["output_schema"]
    assert record["risk_score"] == plan.risk
    assert record["vote"] == "APPROVE"
    assert record["flag_human_review"] == (plan.risk > 20)

    # The CSV transcript must reproduce the real transcript, row for row.
    with open(tmp_path / "council_transcript.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(engine.transcript)
    assert [(r["sender"], r["content"]) for r in rows] == [
        (m.sender.value, m.content) for m in engine.transcript
    ]

    # The JSON transcript must likewise carry the real messages.
    transcript_json = json.loads(
        (tmp_path / "council_transcript.json").read_text(encoding="utf-8")
    )
    assert [m["content"] for m in transcript_json] == [m.content for m in engine.transcript]

    # Plans history is the actual list of drafted plan versions.
    history = json.loads((tmp_path / "plans_history.json").read_text(encoding="utf-8"))
    assert isinstance(history, list) and history
    assert [p["version"] for p in history] == [p.version for p in engine.commander.plans]


# --- LLM call telemetry: bounded retries, safe errors, export ---------------

def test_connection_error_fails_over_without_retrying(monkeypatch):
    client = LLMClient(provider="gemini")
    calls = {"n": 0}

    def boom(system, user):
        calls["n"] += 1
        raise ConnectionError("503 service unavailable")

    monkeypatch.setattr(client, "_call", boom)
    result = client.complete_json("system", "user", retries=2)

    assert result.ok is False
    assert result.kind == "connection"
    assert calls["n"] == 1, "connection errors must fail over without retrying"


def test_generic_error_gets_one_bounded_retry_then_fails_over(monkeypatch):
    client = LLMClient(provider="openai")
    calls = {"n": 0}

    def boom(system, user):
        calls["n"] += 1
        raise ValueError("unexpected payload")

    monkeypatch.setattr(client, "_call", boom)
    result = client.complete_json("system", "user", retries=1)

    assert result.ok is False
    assert calls["n"] == 2, "an unexpected error gets exactly one bounded retry"
    assert result.kind == "error"


def test_call_log_records_outcomes_and_redacts_secrets(monkeypatch):
    client = LLMClient(provider="openai")
    secret = "sk-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    def fail_after_url(system, user):
        raise ConnectionError(f"GET https://api.example.com/v1?key={secret} failed")

    monkeypatch.setattr(client, "_call", fail_after_url)
    assert client.complete_json("system", "user").ok is False

    monkeypatch.setattr(client, "_call", lambda s, u: '{"status": "ok"}')
    assert client.complete_json("system", "user").ok is True

    log = client.call_log
    assert len(log) == 2
    assert log[0]["ok"] is False and log[0]["kind"] == "connection"
    assert log[1]["ok"] is True and log[1]["kind"] == ""
    assert all("elapsed" in e and float(e["elapsed"]) >= 0 for e in log)

    blob = json.dumps(log)
    assert secret not in blob, "the API key must never reach the telemetry log"
    assert "<redacted" in blob


def test_last_error_safe_truncates_and_redacts(monkeypatch):
    client = LLMClient(provider="gemini")
    fake_key = "AIzaTHISISAFakeGoogleAPIKey0123456789abcdef"

    def boom(system, user):
        raise ConnectionError(fake_key + " " + "x" * 300)

    monkeypatch.setattr(client, "_call", boom)
    client.complete_json("system", "user")

    safe = client.last_error_safe
    assert fake_key not in safe
    assert len(safe) <= 200
    assert "<redacted-key>" in safe


def test_failed_live_run_records_telemetry_and_labels_fallback(monkeypatch):
    # A provider outage all the way through the engine: the negotiation must
    # still finish, every vote must be labelled fallback, and the call log must
    # record the failures.
    client = LLMClient(provider="gemini")

    def boom(system, user):
        raise ConnectionError("simulated provider outage")

    monkeypatch.setattr(client, "_call", boom)
    engine = AresEngine(llm=client)
    outcome = engine.start()

    assert outcome.status == "APPROVED", outcome.detail
    assert engine.llm.call_log, "the failed calls must be recorded"
    assert all(not c["ok"] for c in engine.llm.call_log)
    assert all(c["kind"] == "connection" for c in engine.llm.call_log)
    assert all(v.engine == "fallback" for v in engine.commander.all_votes)


def test_exports_include_model_call_log(tmp_path):
    engine = _offline_engine()
    engine.start()
    engine.export(str(tmp_path))

    path = tmp_path / "llm_operations_log.json"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == engine.llm.call_log
