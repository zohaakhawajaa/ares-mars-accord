"""Tests for the deterministic solver."""

from ares.packages import (
    BASELINE_PATH_A,
    BASELINE_PATH_B,
    DEFAULT_RESOURCES,
    DEPARTMENTS,
    MODES,
    RESOURCE_ORDER,
)
from ares.solver import (
    _totals_and_risk,
    all_combinations,
    best_plan,
    enumerate_feasible,
    infeasibility_report,
)


def test_81_combinations():
    combos = all_combinations()
    assert len(combos) == 81

    # Every combination picks exactly one valid mode for every department...
    for combo in combos:
        assert set(combo.keys()) == set(DEPARTMENTS)
        for dept, mode_id in combo.items():
            assert MODES[mode_id].department == dept

    # ...all 81 are distinct...
    assert len({tuple(sorted(c.items())) for c in combos}) == 81

    # ...and the solver's totals/risk/sacrifice arithmetic is correct for all 81.
    for combo in combos:
        totals, risk, sacrifices = _totals_and_risk(combo)
        expected = {k: 0 for k in RESOURCE_ORDER}
        for mode_id in combo.values():
            vector = MODES[mode_id].as_vector()
            for k in RESOURCE_ORDER:
                expected[k] += vector[k]
        assert totals == expected
        assert risk == sum(MODES[m].risk for m in combo.values())
        assert sacrifices == [d for d, m in combo.items() if MODES[m].is_sacrifice]


def test_baseline_pool_is_feasible_at_one_sacrifice():
    plans = enumerate_feasible(DEFAULT_RESOURCES, 24, 1)
    assert plans, "Expected at least one feasible baseline plan"
    # The two documented baseline paths must appear among feasible plans.
    sel_sets = [plan.selections for plan in plans]
    assert BASELINE_PATH_A in sel_sets
    assert BASELINE_PATH_B in sel_sets


def test_best_plan_uses_single_sacrifice_and_valid_limits():
    plan = best_plan(DEFAULT_RESOURCES, 24, 1)
    assert plan is not None
    assert len(plan.sacrifices) == 1
    assert plan.risk <= 24
    for k, v in plan.totals.items():
        assert v <= DEFAULT_RESOURCES[k]


def test_no_feasible_plan_when_pool_crushed():
    crushed = {"power": 40, "water": 20, "oxygen": 20, "robot": 10, "bandwidth": 5}
    assert best_plan(crushed, 24, 2) is None


def test_infeasibility_report_requests_clear_change():
    # Solar aftershock practice event: power -4 makes the 1-sacrifice baseline
    # paths (which need 79) infeasible, but a 2-sacrifice override fits.
    resources = dict(DEFAULT_RESOURCES)
    resources["power"] -= 4
    report = infeasibility_report(resources, 24, 1)
    assert report.resource_shortfalls
    assert report.min_sacrifices_needed == 2
    assert "Override" in report.required_policy_change or "second sacrifice" in report.required_policy_change


def test_infeasibility_when_no_combination_fits():
    crushed = {"power": 50, "water": 30, "oxygen": 30, "robot": 10, "bandwidth": 5}
    report = infeasibility_report(crushed, 24, 2)
    assert report.min_sacrifices_needed is None
    assert "Increase raw resources" in report.required_policy_change


def test_crisis_override_unlocks_second_sacrifice():
    resources = dict(DEFAULT_RESOURCES)
    resources["power"] -= 4
    plan = best_plan(resources, 28, 2)
    assert plan is not None
    assert len(plan.sacrifices) == 2
    assert plan.risk <= 28
