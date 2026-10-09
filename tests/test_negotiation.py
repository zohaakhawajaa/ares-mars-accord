"""Integration tests for the multi-agent negotiation engine (offline fallback)."""

from ares.engine import AresEngine
from ares.llm import LLMClient
from ares.models import DEPARTMENT_ROLES, MessageType, PlanStatus, VoteDecision
from ares.validator import validate_plan


def _offline_engine() -> AresEngine:
    return AresEngine(llm=LLMClient(provider="fallback"))


def test_initial_negotiation_approves_after_required_rounds():
    engine = _offline_engine()
    outcome = engine.start()

    assert outcome.status == "APPROVED"
    assert outcome.rounds >= 3, "baseline requires at least three rounds before approval"
    proc = outcome.process
    assert proc["rejected_or_revised_proposal"] is True
    assert proc["sacrifice_refused_by_an_agent"] is True
    # Every sacrificing department needs two accepted commitments.
    assert proc["accepted_returns_per_sacrifice"].get("Life Support") == 2


def test_final_plan_passes_full_validator():
    engine = _offline_engine()
    engine.start()
    plan = engine.final_plan
    assert plan is not None and plan.status == PlanStatus.APPROVED

    result = validate_plan(
        selections=plan.selections,
        resources=engine.state.resources,
        risk_limit=24,
        max_sacrifices=1,
        plan_version=plan.version,
        commitments=plan.commitments,
        votes=engine.commander.all_votes,
        require_votes=True,
    )
    assert result.passed, result.failed_reasons()
    assert len(plan.sacrifices) == 1
    assert plan.risk <= 24


def test_four_accepts_on_same_version():
    engine = _offline_engine()
    outcome = engine.start()
    version = outcome.plan.version
    accepts = [
        v for v in engine.commander.all_votes
        if v.plan_version == version and v.decision == VoteDecision.ACCEPT
    ]
    agents = {v.agent.value for v in accepts}
    assert agents == {r.value for r in DEPARTMENT_ROLES}


def test_agents_are_stateful_and_exchange_messages():
    engine = _offline_engine()
    engine.start()

    # Each department agent built its own history and message traffic.
    for role in DEPARTMENT_ROLES:
        agent = engine.council.get(role)
        assert len(agent.history) >= 1
        assert agent.state["votes_cast"], f"{role.value} never voted"

    senders = {m.sender.value for m in engine.transcript}
    for role in DEPARTMENT_ROLES:
        assert role.value in senders, f"{role.value} never sent a message"

    types = {m.type for m in engine.transcript}
    for required in (MessageType.PROPOSAL, MessageType.OBJECTION,
                     MessageType.COMMITMENT, MessageType.VOTE, MessageType.APPROVAL):
        assert required in types, f"transcript missing {required}"


def test_plan_versioning_increases():
    engine = _offline_engine()
    engine.start()
    versions = [p.version for p in engine.commander.plans]
    assert versions == sorted(versions)
    assert len(versions) >= 3, "expect at least three drafted plan versions"


def test_single_vote_rejection_recovers_without_deadlock():
    """An LLM-style REJECT must trigger a revise + re-vote, not a deadlock."""
    from ares.models import Role, Vote, VoteDecision

    engine = _offline_engine()
    agent = engine.council.get(Role.ENGINEERING)
    original_vote = agent.vote
    state = {"rejected": False}

    def vote_once_reject(ctx):
        if not state["rejected"] and ctx.plan_version >= 3:
            state["rejected"] = True
            return Vote(agent=Role.ENGINEERING, plan_version=ctx.plan_version,
                        decision=VoteDecision.REJECT, reason="forced test reject", round=ctx.round)
        return original_vote(ctx)

    agent.vote = vote_once_reject
    outcome = engine.start()

    assert state["rejected"] is True, "test did not exercise the reject path"
    assert outcome.status == "APPROVED", f"rejection caused {outcome.status}"
    plan = engine.final_plan
    assert plan is not None and plan.status == PlanStatus.APPROVED

