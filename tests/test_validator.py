"""Tests for the deterministic validator."""

from ares.models import Commitment, CommitmentStatus, Role, Vote, VoteDecision
from ares.packages import BASELINE_PATH_A, DEFAULT_RESOURCES
from ares.validator import (
    validate_mode_packages,
    validate_resources,
    validate_return_agreement,
    validate_risk,
    validate_votes,
    validate_plan,
)


def _commitments_for(owner_a: Role, owner_b: Role, beneficiary: Role, version: int = 1):
    return [
        Commitment(owner=owner_a, beneficiary=beneficiary, return_term="term A",
                   status=CommitmentStatus.ACCEPTED, plan_version=version),
        Commitment(owner=owner_b, beneficiary=beneficiary, return_term="term B",
                   status=CommitmentStatus.ACCEPTED, plan_version=version),
    ]


def _four_accepts(version: int):
    from ares.models import DEPARTMENT_ROLES
    return [Vote(agent=r, plan_version=version, decision=VoteDecision.ACCEPT) for r in DEPARTMENT_ROLES]


def test_mode_package_check_rejects_missing_department():
    check = validate_mode_packages({"Medical": "M2"})
    assert not check.passed
    assert "no selected mode" in check.reason


def test_mode_package_check_rejects_wrong_department():
    selections = dict(BASELINE_PATH_A)
    selections["Medical"] = "L2"  # Life Support mode in the Medical slot
    check = validate_mode_packages(selections)
    assert not check.passed
    assert "not Medical" in check.reason


def test_resources_pass_on_baseline():
    check, totals = validate_resources(BASELINE_PATH_A, DEFAULT_RESOURCES)
    assert check.passed
    assert totals["power"] == 79


def test_resources_fail_when_over_pool():
    all_standard = {"Life Support": "L1", "Medical": "M1", "Food Production": "F1", "Engineering": "E1"}
    check, totals = validate_resources(all_standard, DEFAULT_RESOURCES)
    assert not check.passed
    assert "Pool exceeded" in check.reason


def test_risk_and_sacrifice_limits():
    check, risk, sac = validate_risk(BASELINE_PATH_A, 24, 1)
    assert check.passed and risk == 24 and sac == ["Life Support"]

    check2, _, _ = validate_risk(BASELINE_PATH_A, 20, 1)
    assert not check2.passed and "risk" in check2.reason

    check3, _, _ = validate_risk(BASELINE_PATH_A, 24, 0)
    assert not check3.passed and "sacrifice" in check3.reason


def test_return_agreement_requires_two_distinct_agents():
    from ares.models import Role
    # Only one accepted commitment -> fail
    one = [Commitment(owner=Role.ENGINEERING, beneficiary=Role.LIFE_SUPPORT,
                      return_term="robot", status=CommitmentStatus.ACCEPTED, plan_version=1)]
    check = validate_return_agreement(BASELINE_PATH_A, one, 1)
    assert not check.passed

    two = _commitments_for(Role.ENGINEERING, Role.COMMANDER, Role.LIFE_SUPPORT, version=1)
    check2 = validate_return_agreement(BASELINE_PATH_A, two, 1)
    assert check2.passed

    # A commitment offered but not accepted must not count.
    offered = [
        Commitment(owner=Role.ENGINEERING, beneficiary=Role.LIFE_SUPPORT,
                   return_term="robot", status=CommitmentStatus.OFFERED, plan_version=1),
        Commitment(owner=Role.COMMANDER, beneficiary=Role.LIFE_SUPPORT,
                   return_term="priority", status=CommitmentStatus.ACCEPTED, plan_version=1),
    ]
    check3 = validate_return_agreement(BASELINE_PATH_A, offered, 1)
    assert not check3.passed


def test_return_agreement_survives_plan_revision():
    # Commitments agreed on an earlier version must keep the revised plan safe,
    # otherwise the vote-retry / revise path would deadlock.
    two = _commitments_for(Role.ENGINEERING, Role.COMMANDER, Role.LIFE_SUPPORT, version=3)
    check = validate_return_agreement(BASELINE_PATH_A, two, 4)
    assert check.passed, check.reason

    # A commitment made for a *later* version must not satisfy an earlier plan.
    future = _commitments_for(Role.ENGINEERING, Role.COMMANDER, Role.LIFE_SUPPORT, version=9)
    check2 = validate_return_agreement(BASELINE_PATH_A, future, 4)
    assert not check2.passed


def test_votes_must_match_same_version():
    ok = validate_votes(_four_accepts(2), 2)
    assert ok.passed

    stale = _four_accepts(1)
    check = validate_votes(stale, 2)
    assert not check.passed and "Missing ACCEPT" in check.reason


def test_vote_reject_blocks_approval():
    from ares.models import DEPARTMENT_ROLES
    votes = _four_accepts(1)
    votes[0] = Vote(agent=DEPARTMENT_ROLES[0], plan_version=1, decision=VoteDecision.REJECT, reason="unfair")
    check = validate_votes(votes, 1)
    assert not check.passed and "REJECT" in check.reason


def test_full_plan_passes_only_when_everything_is_correct():
    commitments = _commitments_for(Role.ENGINEERING, Role.COMMANDER, Role.LIFE_SUPPORT, version=1)
    votes = _four_accepts(1)

    good = validate_plan(BASELINE_PATH_A, DEFAULT_RESOURCES, 24, 1, 1, commitments, votes)
    assert good.passed

    bad_votes = validate_plan(BASELINE_PATH_A, DEFAULT_RESOURCES, 24, 1, 1, commitments, [])
    assert not bad_votes.passed

    no_returns = validate_plan(BASELINE_PATH_A, DEFAULT_RESOURCES, 24, 1, 1, [], votes)
    assert not no_returns.passed
    assert any("Return agreement" in r for r in no_returns.failed_reasons())
