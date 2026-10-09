"""Tests for event parsing, stale-plan marking and crisis recovery."""

from ares.engine import AresEngine, parse_event
from ares.llm import LLMClient
from ares.models import DEPARTMENT_ROLES, PlanStatus, VoteDecision
from ares.packages import DEFAULT_RESOURCES


def _offline_engine() -> AresEngine:
    engine = AresEngine(llm=LLMClient(provider="fallback"))
    engine.start()
    return engine


def test_parse_event_supports_fixed_and_percent():
    fixed = parse_event({"event_id": "E1", "impact": {"power_reduction": 4}}, DEFAULT_RESOURCES)
    assert fixed.resource_deltas == {"power": -4}

    pct = parse_event({"event_id": "E2", "impact": {"power_reduction_percent": 10}}, DEFAULT_RESOURCES)
    assert pct.resource_deltas["power"] == -8  # round(79 * 0.10) = 8

    direct = parse_event({"event_id": "E3", "resource_deltas": {"water": -3}}, DEFAULT_RESOURCES)
    assert direct.resource_deltas == {"water": -3}


def test_solar_aftershock_recovers_with_override():
    engine = _offline_engine()
    initial_version = engine.final_plan.version

    outcome = engine.inject_event({
        "event_id": "CRISIS_002",
        "event_name": "Solar Aftershock",
        "description": "Power drops",
        "impact": {"power_reduction": 4},
    })

    assert outcome.status == "APPROVED"
    assert outcome.rounds >= 2, "post-event negotiation must run at least two visible rounds"
    assert engine.state.override_active is True
    assert outcome.plan.version > initial_version, "recovery must produce a new plan version"
    assert outcome.plan.risk <= 28

    # Old approved plan was marked stale / invalid.
    old = next(p for p in engine.commander.plans if p.version == initial_version)
    assert old.status in (PlanStatus.STALE, PlanStatus.INVALID)

    # Prior commitments expired and fresh ones were negotiated.
    assert any(c.status.value == "EXPIRED" for c in engine.commander.commitments)
    assert outcome.process["accepted_returns_per_sacrifice"]


def test_recovery_requires_fresh_votes_on_the_new_version():
    engine = _offline_engine()
    initial_version = engine.final_plan.version

    outcome = engine.inject_event({
        "event_id": "CRISIS_007",
        "event_name": "Solar Aftershock",
        "impact": {"power_reduction": 4},
    })
    assert outcome.status == "APPROVED"
    new_version = outcome.plan.version
    assert new_version > initial_version

    # Fresh votes were cast on the NEW version: all four departments ACCEPT it.
    fresh = [
        v for v in engine.commander.all_votes
        if v.plan_version == new_version and v.decision is VoteDecision.ACCEPT
    ]
    assert {v.agent for v in fresh} == set(DEPARTMENT_ROLES)
    # The old version's votes exist but do not satisfy the new version.
    stale = [v for v in engine.commander.all_votes if v.plan_version == initial_version]
    assert stale, "the first negotiation must have recorded votes"


def test_crisis_log_records_the_event():
    engine = _offline_engine()
    engine.inject_event({"event_id": "CRISIS_003", "event_name": "Relay Interference",
                         "impact": {"bandwidth_reduction": 2}})
    assert len(engine.crisis_log) == 1
    entry = engine.crisis_log[0]
    assert entry["event"]["event_id"] == "CRISIS_003"
    assert entry["post_event_resources"]["bandwidth"] == DEFAULT_RESOURCES["bandwidth"] - 2


def test_infeasible_event_returns_justified_infeasible():
    engine = _offline_engine()
    # Crush every resource so no combination can possibly fit.
    outcome = engine.inject_event({
        "event_id": "CRISIS_999",
        "event_name": "Catastrophic Coronal Mass Ejection",
        "resource_deltas": {"power": -60, "water": -40, "oxygen": -45, "robot": -20, "bandwidth": -14},
    })
    assert outcome.status == "INFEASIBLE"
    assert outcome.infeasibility is not None
    assert outcome.infeasibility["required_policy_change"]
