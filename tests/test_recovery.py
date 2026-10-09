"""Regression tests for the controlled vote-rejection recovery mechanism.

These cover the failure modes that could previously end in a silent deadlock:
a language model rejecting a valid, unchanged plan forever, a concrete
objection that should reshape the plan, and legitimate safety objections that
must never be overridden.
"""

from ares.agents import AresAgent, NegotiationContext
from ares.engine import AresEngine
from ares.llm import LLMClient
from ares.models import Role, Vote, VoteDecision
from ares.packages import BASELINE_PATH_A, DEFAULT_RESOURCES
from ares.validator import validate_plan, validate_return_agreement, validate_votes


def _offline_engine() -> AresEngine:
    return AresEngine(llm=LLMClient(provider="fallback"))


# 1. An agent repeatedly rejecting an unchanged, validator-safe plan.
def test_repeated_unsupported_rejection_recovers_without_version_creep():
    engine = _offline_engine()
    medical = engine.council.get(Role.MEDICAL)

    def fake_ask(ctx, task, schema):
        if "Vote on the current plan" in task:
            return {"decision": "REJECT", "reason": "the resource pool is unsafe and unfair"}
        return None

    medical._ask_llm = fake_ask
    outcome = engine.start()

    assert outcome.status == "APPROVED", outcome.detail
    # No meaningful change was available, so no new plan version was invented.
    assert outcome.plan.version == 3
    assert outcome.process["plan_versions"] == [1, 2, 3]
    assert outcome.process["reconsiderations"] >= 1
    # Recovery used the agent's own clearly-labelled deterministic policy.
    assert any(
        v.engine == "fallback" and v.decision == VoteDecision.ACCEPT
        for v in engine.commander.all_votes
    )


# 2. A rejection that identifies a concrete issue and causes a real revision.
def test_concrete_objection_triggers_meaningful_revision():
    engine = _offline_engine()
    life = engine.council.get(Role.LIFE_SUPPORT)

    def fake_ask(ctx, task, schema):
        if "Vote on the current plan" in task and ctx.plan_selections.get("Life Support") == "L3":
            return {
                "decision": "REJECT",
                "reason": "I refuse L3. Give me L2 and drop the Life Support sacrifice.",
            }
        return None

    life._ask_llm = fake_ask
    outcome = engine.start()

    assert outcome.status == "APPROVED", outcome.detail
    assert outcome.process["meaningful_revisions"] >= 1
    assert outcome.plan.version > 3, "a real revision must create a new plan version"
    sel = outcome.plan.selections
    assert sel["Life Support"] != "L3"
    assert "E3" in sel.values(), "the alternative feasible plan should now sacrifice Engineering"


# 3. A legitimate safety objection must never be overridden.
def test_legitimate_safety_objection_is_not_overridden():
    # L3 sacrifice with no accepted returns -> validator fails return agreement.
    bad = validate_plan(BASELINE_PATH_A, DEFAULT_RESOURCES, 24, 1, 1, [], [], require_votes=False)
    assert not bad.passed

    for deceptive in ("ACCEPT", "REJECT"):
        agent = AresAgent(Role.MEDICAL, llm=None)
        agent._ask_llm = lambda ctx, task, schema, d=deceptive: {"decision": d, "reason": "looks fine"}
        ctx = NegotiationContext(
            scenario_id="TEST", resources=DEFAULT_RESOURCES, risk_limit=24,
            max_sacrifices=1, round=4, plan_version=1, phase="vote",
            plan_selections=BASELINE_PATH_A, validation=bad,
        )
        vote = agent.vote(ctx)
        assert vote.decision == VoteDecision.REJECT, "an invalid plan may never be accepted"


# 4. Votes must not carry over to a different plan version.
def test_votes_do_not_carry_to_a_new_plan_version():
    engine = _offline_engine()
    engine.start()
    version = engine.final_plan.version
    current_votes = [v for v in engine.commander.all_votes if v.plan_version == version]
    assert validate_votes(current_votes, version).passed
    assert not validate_votes(current_votes, version + 1).passed


# 5. Accepted return commitments survive a valid plan revision.
def test_accepted_returns_survive_a_valid_revision():
    engine = _offline_engine()
    engine.start()
    commander = engine.commander
    accepted = [c for c in commander.commitments if c.is_accepted()]
    assert accepted

    plan, new_accepted = commander._adopt_candidate(
        engine.final_plan.selections, note="same-beneficiary revision", carry=accepted
    )
    assert len(new_accepted) >= 2
    keys = [(c.beneficiary.value, c.owner.value) for c in new_accepted]
    assert len(keys) == len(set(keys)), "returns must not be fabricated twice"
    assert validate_return_agreement(plan.selections, new_accepted, plan.version).passed


# 6. Crisis events expire commitments so they cannot satisfy a new crisis.
def test_crisis_event_expires_prior_commitments():
    engine = _offline_engine()
    engine.start()
    prior = [c for c in engine.commander.commitments if c.is_accepted()]
    assert prior

    outcome = engine.inject_event({
        "event_id": "CRISIS_006",
        "event_name": "Solar Aftershock",
        "impact": {"power_reduction": 4},
    })
    assert outcome.status == "APPROVED", outcome.detail
    assert all(c.status.value == "EXPIRED" for c in prior)
    plan = engine.final_plan
    assert validate_return_agreement(plan.selections, plan.commitments, plan.version).passed


# 7. Negotiation always terminates within the configured maximum rounds.
def test_negotiation_terminates_within_max_rounds():
    engine = AresEngine(llm=LLMClient(provider="fallback"))
    engine.state.max_rounds = 4

    engineering = engine.council.get(Role.ENGINEERING)
    engineering.vote = lambda ctx: Vote(
        agent=Role.ENGINEERING, plan_version=ctx.plan_version,
        decision=VoteDecision.REJECT, reason="no", round=ctx.round,
    )

    outcome = engine.start()
    assert outcome.status in ("APPROVED", "DEADLOCK")
    assert outcome.rounds <= engine.state.max_rounds
