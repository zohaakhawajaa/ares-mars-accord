"""Tests for the fixed mode packages against the challenge guide."""

from ares.packages import (
    BASELINE_PATH_A,
    BASELINE_PATH_B,
    DEFAULT_RESOURCES,
    MODES,
    MODE_TABLE,
    SelectionSet,
)


EXPECTED_TABLE = {
    "Life Support": {"L1": (34, 10, 42, 4, 4, 1), "L2": (27, 9, 36, 6, 3, 5), "L3": (23, 8, 33, 2, 2, 9)},
    "Medical": {"M1": (28, 12, 18, 3, 10, 1), "M2": (21, 10, 14, 2, 6, 5), "M3": (18, 8, 12, 2, 5, 9)},
    "Food Production": {"F1": (24, 34, 8, 4, 2, 1), "F2": (15, 27, 5, 4, 2, 5), "F3": (10, 24, 5, 2, 1, 9)},
    "Engineering": {"E1": (26, 6, 6, 22, 10, 1), "E2": (20, 5, 5, 18, 7, 5), "E3": (16, 4, 4, 14, 5, 9)},
}


def test_mode_table_matches_guide_exactly():
    assert MODE_TABLE == EXPECTED_TABLE


def test_resource_pool_matches_guide():
    assert DEFAULT_RESOURCES == {"power": 79, "water": 52, "oxygen": 59, "robot": 26, "bandwidth": 17}


def test_sacrifice_flags():
    assert MODES["L3"].is_sacrifice
    assert MODES["M3"].is_sacrifice
    assert MODES["F3"].is_sacrifice
    assert MODES["E3"].is_sacrifice
    assert not MODES["L1"].is_sacrifice
    assert not MODES["E2"].is_sacrifice


def test_baseline_path_a_totals():
    sel = SelectionSet(BASELINE_PATH_A)
    totals = sel.totals()
    assert totals == {"power": 79, "water": 50, "oxygen": 57, "robot": 26, "bandwidth": 17}
    assert sel.total_risk() == 24
    assert sel.sacrifices() == ["Life Support"]


def test_baseline_path_b_totals():
    sel = SelectionSet(BASELINE_PATH_B)
    totals = sel.totals()
    assert totals == {"power": 79, "water": 50, "oxygen": 59, "robot": 26, "bandwidth": 16}
    assert sel.total_risk() == 24
    assert sel.sacrifices() == ["Engineering"]
