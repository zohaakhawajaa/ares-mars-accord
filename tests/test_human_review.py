"""Tests for the optional Human-in-the-Loop approval gate.

The human gate is only ever reachable AFTER the deterministic validator passes
and four ACCEPT votes are present. A human can never approve an unsafe plan.
"""

from ares.engine import AresEngine
from ares.llm import LLMClient
from ares.models import DEPARTMENT_ROLES, PlanStatus, Role, Vote, VoteDecision


def _human_review_engine() -> AresEngine:
    engine = AresEngine(llm=LLMClient(provider="fallback"))
    engine.set_human_review(True)
    return engine


def test_high_risk_plan_is_held_for_human_review():
    engine = _human_review_engine()
    outcome = engine.start()

    assert outcome.status == "PENDING_HUMAN_REVIEW"
    assert engine.human_review_pending is True
    plan = engine.commander.pending_human_plan
    assert plan is not None
    assert plan.status is not PlanStatus.APPROVED
    assert plan.risk > 20
    # The gate is only reachable because validation passed and all four voted.
    assert plan.validation is not None and plan.validation.passed
    accepts = {
        v.agent for v in engine.commander.all_votes
        if v.plan_version == plan.version and v.decision == VoteDecision.ACCEPT
    }
    assert accepts == set(DEPARTMENT_ROLES)


def test_human_approval_completes_a_valid_plan():
    engine = _human_review_engine()
    engine.start()
    pending_version = engine.commander.pending_human_plan.version

    ok, _ = engine.approve_as_human()

    assert ok is True
    assert engine.human_review_pending is False
    assert engine.state.status == "APPROVED"
    assert engine.final_plan is not None
    assert engine.final_plan.version == pending_version
    assert engine.final_plan.status is PlanStatus.APPROVED


def test_human_cannot_bypass_the_validator():
    engine = _human_review_engine()
    engine.start()
    plan = engine.commander.pending_human_plan

    # Corrupt the vote set: a REJECT appears on the current version.
    engine.commander.all_votes.append(
        Vote(agent=Role.MEDICAL, plan_version=plan.version,
             decision=VoteDecision.REJECT, reason="injected test reject")
    )

    ok, message = engine.approve_as_human()

    assert ok is False
    assert "validator" in message.lower()
    assert plan.status is not PlanStatus.APPROVED
    assert engine.human_review_pending is True  # still awaiting a valid decision
    assert engine.final_plan is None


def test_human_rejection_is_recorded():
    engine = _human_review_engine()
    engine.start()

    ok, _ = engine.reject_as_human("risk too high")

    assert ok is True
    assert engine.state.status == "REJECTED_BY_HUMAN"
    assert engine.commander.human_decision == "REJECTED"
    assert engine.human_review_pending is False
    assert engine.final_plan is None, "a rejected plan must never be recorded as final"
    assert engine.commander.pending_human_plan is None


def test_injecting_event_invalidates_pending_human_review():
    engine = _human_review_engine()
    engine.start()
    old = engine.commander.pending_human_plan
    old_version = old.version
    assert old.status is PlanStatus.FEASIBLE

    outcome = engine.inject_event({
        "event_id": "CRISIS_010",
        "event_name": "Solar Aftershock",
        "impact": {"power_reduction": 4},
    })

    # The superseded plan is invalidated and can no longer be approved.
    assert old.status is PlanStatus.STALE
    assert engine.commander.pending_human_plan is not old
    # The new crisis produces its own fresh review on a new plan version.
    assert outcome.status == "PENDING_HUMAN_REVIEW"
    new_pending = engine.commander.pending_human_plan
    assert new_pending is not None and new_pending.version > old_version


def test_hitl_disabled_by_default_auto_approves():
    engine = AresEngine(llm=LLMClient(provider="fallback"))
    assert engine.commander.require_human_review is False

    outcome = engine.start()

    assert outcome.status == "APPROVED"
    assert engine.human_review_pending is False
    assert engine.final_plan is not None


def test_low_risk_plan_does_not_require_review():
    # A generous pool lets every department run Standard mode (risk 4 <= 20).
    engine = _human_review_engine()
    ample = {"power": 200, "water": 200, "oxygen": 200, "robot": 200, "bandwidth": 200}
    outcome = engine.start(resources=ample)

    assert outcome.status == "APPROVED"
    assert engine.human_review_pending is False
    assert engine.final_plan.risk <= 20


def test_approve_without_pending_plan_is_rejected():
    engine = _human_review_engine()
    ok, message = engine.approve_as_human()
    assert ok is False
    assert "no plan" in message.lower()
