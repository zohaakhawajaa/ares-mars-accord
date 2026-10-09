"""Deterministic plan solver.

There are only 3^4 = 81 possible mode combinations, so we can exhaustively
enumerate them instantly. This gives the Commander a trustworthy source of
candidate plans (and a precise infeasibility explanation) without ever asking
a language model to do arithmetic.

The solver is independent of the negotiation: agents may *prefer* plans, but
feasibility itself is decided here and re-checked by :mod:`ares.validator`.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .packages import (
    CRISIS_OVERRIDE_MAX_SACRIFICES,
    CRISIS_OVERRIDE_RISK_LIMIT,
    DEPARTMENTS,
    MODES,
    RESOURCE_ORDER,
)

# Departments we try hardest to keep out of Sacrifice mode, most important
# first. Used only as a deterministic tie-breaker between otherwise equal
# feasible plans.
DEFAULT_PROTECTED: Tuple[str, ...] = (
    "Medical",
    "Life Support",
    "Food Production",
    "Engineering",
)


@dataclass
class CandidatePlan:
    selections: Dict[str, str]
    totals: Dict[str, int]
    risk: int
    sacrifices: List[str]
    score: Tuple[int, ...]

    def as_dict(self) -> Dict[str, object]:
        return {
            "selections": dict(self.selections),
            "totals": dict(self.totals),
            "risk": self.risk,
            "sacrifices": list(self.sacrifices),
        }


def all_combinations() -> List[Dict[str, str]]:
    """Every one-mode-per-department assignment (81 of them)."""
    per_dept = [
        [mid for mid, m in MODES.items() if m.department == dept]
        for dept in DEPARTMENTS
    ]
    combos = []
    for choice in itertools.product(*per_dept):
        combos.append({dept: mid for dept, mid in zip(DEPARTMENTS, choice)})
    return combos


def _totals_and_risk(selections: Dict[str, str]):
    totals = {k: 0 for k in RESOURCE_ORDER}
    risk = 0
    sacrifices: List[str] = []
    for dept in DEPARTMENTS:
        m = MODES[selections[dept]]
        v = m.as_vector()
        for k in RESOURCE_ORDER:
            totals[k] += v[k]
        risk += m.risk
        if m.is_sacrifice:
            sacrifices.append(dept)
    return totals, risk, sacrifices


def _fits(totals: Dict[str, int], resources: Dict[str, int]) -> bool:
    return all(totals[k] <= resources.get(k, 0) for k in RESOURCE_ORDER)


def _score(
    totals: Dict[str, int],
    risk: int,
    sacrifices: List[str],
    resources: Dict[str, int],
    protected: Sequence[str],
) -> Tuple[int, ...]:
    # Lower is better. Lexicographic ordering.
    protect_rank = {d: i for i, d in enumerate(protected)}
    sacrifice_penalty = sum(protect_rank.get(d, len(protected)) for d in sacrifices)
    # Prefer a little bandwidth/robot slack preserved, then tighter overall.
    slack = sum(resources.get(k, 0) - totals[k] for k in RESOURCE_ORDER)
    return (len(sacrifices), risk, sacrifice_penalty, -slack)


def enumerate_feasible(
    resources: Dict[str, int],
    risk_limit: int,
    max_sacrifices: int,
    protected: Sequence[str] = DEFAULT_PROTECTED,
) -> List[CandidatePlan]:
    """Return all feasible plans, best first (see :func:`_score`)."""
    results: List[CandidatePlan] = []
    for selections in all_combinations():
        totals, risk, sacrifices = _totals_and_risk(selections)
        if len(sacrifices) > max_sacrifices:
            continue
        if risk > risk_limit:
            continue
        if not _fits(totals, resources):
            continue
        results.append(
            CandidatePlan(
                selections=selections,
                totals=totals,
                risk=risk,
                sacrifices=sacrifices,
                score=_score(totals, risk, sacrifices, resources, protected),
            )
        )
    results.sort(key=lambda c: (c.score, sorted(c.selections.values())))
    return results


def best_plan(
    resources: Dict[str, int],
    risk_limit: int,
    max_sacrifices: int,
    protected: Sequence[str] = DEFAULT_PROTECTED,
    preferred_sacrifice: Optional[str] = None,
) -> Optional[CandidatePlan]:
    """Best feasible plan, optionally biased toward a department volunteering."""
    plans = enumerate_feasible(resources, risk_limit, max_sacrifices, protected)
    if not plans:
        return None
    if preferred_sacrifice:
        for plan in plans:
            if preferred_sacrifice in plan.sacrifices:
                return plan
    return plans[0]


@dataclass
class InfeasibilityReport:
    reason: str
    best_violating_plan: Optional[Dict[str, str]]
    resource_shortfalls: Dict[str, int]
    min_risk: int
    min_sacrifices_needed: Optional[int]
    required_policy_change: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "reason": self.reason,
            "best_violating_plan": self.best_violating_plan,
            "resource_shortfalls": self.resource_shortfalls,
            "min_risk": self.min_risk,
            "min_sacrifices_needed": self.min_sacrifices_needed,
            "required_policy_change": self.required_policy_change,
        }


def infeasibility_report(
    resources: Dict[str, int],
    risk_limit: int,
    max_sacrifices: int,
) -> InfeasibilityReport:
    """Explain *why* no plan fits and what single change would unblock it."""
    best = None
    best_violation = None
    for selections in all_combinations():
        totals, risk, sacrifices = _totals_and_risk(selections)
        overflow = sum(max(0, totals[k] - resources.get(k, 0)) for k in RESOURCE_ORDER)
        sac_over = max(0, len(sacrifices) - max_sacrifices)
        risk_over = max(0, risk - risk_limit)
        violation = overflow + sac_over * 1000 + risk_over * 1000
        if best_violation is None or violation < best_violation:
            best_violation = violation
            best = (selections, totals, risk, sacrifices)

    assert best is not None
    selections, totals, risk, sacrifices = best
    shortfalls = {
        k: totals[k] - resources.get(k, 0)
        for k in RESOURCE_ORDER
        if totals[k] > resources.get(k, 0)
    }

    # Absolute lower bound on risk (all departments Standard).
    min_risk = sum(MODES[mid].risk for mid in ("L1", "M1", "F1", "E1"))

    # Find the fewest sacrifices needed to fit the pool while respecting the
    # Crisis Override risk ceiling (28). If even that is impossible, the pool
    # itself cannot support the colony and resources must increase.
    combos = all_combinations()
    min_sacrifices_needed: Optional[int] = None
    for k in range(0, len(DEPARTMENTS) + 1):
        for selections in combos:
            totals, risk, sac = _totals_and_risk(selections)
            if len(sac) <= k and len(sac) <= CRISIS_OVERRIDE_MAX_SACRIFICES:
                if _fits(totals, resources) and risk <= CRISIS_OVERRIDE_RISK_LIMIT:
                    if min_sacrifices_needed is None:
                        min_sacrifices_needed = k
        if min_sacrifices_needed is not None:
            break

    if min_sacrifices_needed is None:
        reason = (
            "No mode combination fits the pool even with two Sacrifice modes and "
            "the Crisis Override risk limit."
        )
        change = (
            "Increase raw resources (see shortfall), or lower at least one "
            "published package's demand. This crisis is genuinely INFEASIBLE."
        )
    else:
        reason = (
            "No mode combination fits the current baseline limits (risk <= "
            f"{risk_limit}, at most {max_sacrifices} sacrifice)."
        )
        change = (
            f"Activate Crisis Override: at least {min_sacrifices_needed} Sacrifice "
            f"mode(s) are required and the risk limit must rise to "
            f"{CRISIS_OVERRIDE_RISK_LIMIT}."
        )

    return InfeasibilityReport(
        reason=reason,
        best_violating_plan=dict(selections),
        resource_shortfalls=shortfalls,
        min_risk=min_risk,
        min_sacrifices_needed=min_sacrifices_needed,
        required_policy_change=change,
    )


def describe_candidate(plan: CandidatePlan) -> str:
    modes = ", ".join(f"{d}={plan.selections[d]}" for d in DEPARTMENTS)
    totals = " / ".join(f"{k[0].upper()}{plan.totals[k]}" for k in RESOURCE_ORDER)
    sac = ", ".join(plan.sacrifices) if plan.sacrifices else "none"
    return f"{modes} | {totals} | risk {plan.risk} | sacrifice: {sac}"
