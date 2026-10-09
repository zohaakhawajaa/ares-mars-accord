"""Deterministic plan validator.

This module NEVER calls a language model. It independently re-computes every
number and re-checks every policy rule from the challenge guide, so the
Commander can never approve an unsafe plan because an agent "said so".

Checks performed (guide section 04 / product requirements):
    1. Mode packages  - exactly one published package per department,
                        package values are unchanged.
    2. Resources      - the summed packages fit inside the current pool.
    3. Risk & sacrifice - combined risk within limit; sacrifice count allowed.
    4. Return agreement - every sacrificing department holds two ACCEPTED
                        commitments from two different agents.
    5. Votes          - all four ACCEPT votes refer to the same current plan
                        version (any plan change must clear earlier votes).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .models import (
    DEPARTMENT_ROLES,
    Commitment,
    CommitmentStatus,
    Plan,
    Role,
    ValidationCheck,
    ValidationResult,
    Vote,
    VoteDecision,
)
from .packages import (
    DEPARTMENTS,
    MODES,
    RESOURCE_ORDER,
)


def _check(name: str, passed: bool, reason: str) -> ValidationCheck:
    return ValidationCheck(name=name, passed=passed, reason=reason)


def validate_mode_packages(selections: Dict[str, str]) -> ValidationCheck:
    problems: List[str] = []
    for dept in DEPARTMENTS:
        if dept not in selections:
            problems.append(f"{dept} has no selected mode")
            continue
        mode_id = selections[dept]
        if mode_id not in MODES:
            problems.append(f"{dept} selected unknown mode '{mode_id}'")
            continue
        if MODES[mode_id].department != dept:
            problems.append(
                f"{mode_id} belongs to {MODES[mode_id].department}, not {dept}"
            )
    extra = [d for d in selections if d not in DEPARTMENTS]
    if extra:
        problems.append(f"unknown departments: {', '.join(extra)}")
    if problems:
        return _check("Mode packages", False, "; ".join(problems))
    chosen = ", ".join(f"{d}={selections[d]}" for d in DEPARTMENTS)
    return _check("Mode packages", True, f"Exactly one unchanged package per department ({chosen})")


def validate_resources(
    selections: Dict[str, str], resources: Dict[str, int]
) -> tuple[ValidationCheck, Dict[str, int]]:
    totals = {k: 0 for k in RESOURCE_ORDER}
    for dept in DEPARTMENTS:
        mode_id = selections.get(dept)
        if mode_id not in MODES:
            # Cannot compute totals; the mode-package check reports this.
            return (
                _check("Resources", False, "Cannot total resources: invalid mode selection"),
                totals,
            )
        vec = MODES[mode_id].as_vector()
        for k in RESOURCE_ORDER:
            totals[k] += vec[k]

    overflow = {k: totals[k] - resources.get(k, 0) for k in RESOURCE_ORDER if totals[k] > resources.get(k, 0)}
    if overflow:
        detail = ", ".join(f"{k} over by {v} ({totals[k]}/{resources.get(k, 0)})" for k, v in overflow.items())
        return _check("Resources", False, f"Pool exceeded: {detail}"), totals
    summary = " / ".join(f"{k[0].upper()}{totals[k]}" for k in RESOURCE_ORDER)
    return _check("Resources", True, f"Within pool ({summary})"), totals


def validate_risk(
    selections: Dict[str, str], risk_limit: int, max_sacrifices: int
) -> tuple[ValidationCheck, int, List[str]]:
    risk = 0
    sacrifices: List[str] = []
    for dept in DEPARTMENTS:
        mode_id = selections.get(dept)
        if mode_id not in MODES:
            return _check("Risk & sacrifice", False, "Cannot compute risk: invalid selection"), 0, []
        risk += MODES[mode_id].risk
        if MODES[mode_id].is_sacrifice:
            sacrifices.append(dept)

    problems: List[str] = []
    if risk > risk_limit:
        problems.append(f"risk {risk} exceeds limit {risk_limit}")
    if len(sacrifices) > max_sacrifices:
        problems.append(
            f"{len(sacrifices)} sacrifice modes used but at most {max_sacrifices} allowed"
        )
    if problems:
        return _check("Risk & sacrifice", False, "; ".join(problems)), risk, sacrifices
    sac_txt = ", ".join(sacrifices) if sacrifices else "none"
    return (
        _check(
            "Risk & sacrifice",
            True,
            f"risk {risk}/{risk_limit}, sacrifices {len(sacrifices)}/{max_sacrifices} ({sac_txt})",
        ),
        risk,
        sacrifices,
    )


def validate_return_agreement(
    selections: Dict[str, str], commitments: Sequence[Commitment], plan_version: int
) -> ValidationCheck:
    sacrifices = [
        dept for dept in DEPARTMENTS
        if selections.get(dept) in MODES and MODES[selections[dept]].is_sacrifice
    ]
    if not sacrifices:
        return _check("Return agreement", True, "No sacrifice mode active; no return required")

    problems: List[str] = []
    for dept in sacrifices:
        owners = set()
        for c in commitments:
            if c.status != CommitmentStatus.ACCEPTED:
                continue
            if c.beneficiary.value != dept:
                continue
            # A return commitment stays valid for the plan version it was made
            # on and later revisions within the same negotiation (0 = any).
            # Event-time expiry is what invalidates commitments across crises.
            if c.plan_version > plan_version:
                continue
            if c.owner.value == dept:
                continue
            owners.add(c.owner.value)
        if len(owners) < 2:
            problems.append(
                f"{dept} has {len(owners)} accepted commitment(s) from different agents; needs 2"
            )
    if problems:
        return _check("Return agreement", False, "; ".join(problems))
    detail = ", ".join(
        f"{dept}<-2 accepted returns" for dept in sacrifices
    )
    return _check("Return agreement", True, f"Fair return secured ({detail})")


def validate_votes(
    votes: Sequence[Vote], plan_version: int, required_voters: Sequence[Role] = DEPARTMENT_ROLES
) -> ValidationCheck:
    if plan_version <= 0:
        return _check("Votes", False, "No plan version to vote on")

    current = [v for v in votes if v.plan_version == plan_version]
    stale = [v for v in votes if v.plan_version != plan_version]
    accepts = {
        v.agent.value: v
        for v in current
        if v.decision == VoteDecision.ACCEPT
    }
    rejects = [v.agent.value for v in current if v.decision == VoteDecision.REJECT]

    missing = [r.value for r in required_voters if r.value not in accepts]
    if rejects:
        return _check("Votes", False, f"REJECT votes on v{plan_version}: {', '.join(rejects)}")
    if missing:
        stale_note = f" ({len(stale)} stale vote(s) from older versions ignored)" if stale else ""
        return _check(
            "Votes",
            False,
            f"Missing ACCEPT on v{plan_version} from: {', '.join(missing)}{stale_note}",
        )
    return _check(
        "Votes",
        True,
        f"All four departments ACCEPT the current plan version v{plan_version}",
    )


def validate_plan(
    selections: Dict[str, str],
    resources: Dict[str, int],
    risk_limit: int,
    max_sacrifices: int,
    plan_version: int,
    commitments: Optional[Sequence[Commitment]] = None,
    votes: Optional[Sequence[Vote]] = None,
    require_votes: bool = True,
) -> ValidationResult:
    """Run every deterministic check and return a structured result.

    Set ``require_votes=False`` to check only structural feasibility (mode
    packages, resources, risk, return agreement) before the council votes.
    """

    commitments = list(commitments or [])
    votes = list(votes or [])

    mode_check = validate_mode_packages(selections)
    resource_check, totals = validate_resources(selections, resources)
    risk_check, risk, sacrifices = validate_risk(selections, risk_limit, max_sacrifices)
    return_check = validate_return_agreement(selections, commitments, plan_version)
    if require_votes:
        vote_check = validate_votes(votes, plan_version)
    else:
        vote_check = _check("Votes", True, "Pending: council has not voted yet")

    checks = [mode_check, resource_check, risk_check, return_check, vote_check]
    passed = all(c.passed for c in checks)

    return ValidationResult(
        passed=passed,
        plan_version=plan_version,
        checks=checks,
        totals=totals,
        risk=risk,
        sacrifices=sacrifices,
    )


def validate_plan_object(plan: Plan, resources: Dict[str, int], risk_limit: int,
                         max_sacrifices: int, votes: Sequence[Vote]) -> ValidationResult:
    """Convenience wrapper around :func:`validate_plan` for a Plan record."""
    return validate_plan(
        selections=plan.selections,
        resources=resources,
        risk_limit=risk_limit,
        max_sacrifices=max_sacrifices,
        plan_version=plan.version,
        commitments=plan.commitments,
        votes=votes,
    )
