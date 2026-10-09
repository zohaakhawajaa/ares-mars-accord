"""Authoritative mode packages for the ARES Accord scenario.

These values are copied verbatim from the participant challenge guide
(v2.1, section 02 / RESOURCES) and MUST NOT be changed or silently relaxed.

Each mode is a fixed package: agents may switch modes, but they may never
trim individual resources inside a package.

Tuple order is intentionally documented to avoid positional mistakes:
    (power, water, oxygen, robot_time, bandwidth, risk)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

RESOURCE_ORDER: Tuple[str, ...] = ("power", "water", "oxygen", "robot", "bandwidth")

# --- Fixed package table -----------------------------------------------------
# department -> {mode_id: (power, water, oxygen, robot, bandwidth, risk)}
MODE_TABLE: Dict[str, Dict[str, Tuple[int, int, int, int, int, int]]] = {
    "Life Support": {
        "L1": (34, 10, 42, 4, 4, 1),  # Standard
        "L2": (27, 9, 36, 6, 3, 5),   # Restricted
        "L3": (23, 8, 33, 2, 2, 9),   # Sacrifice
    },
    "Medical": {
        "M1": (28, 12, 18, 3, 10, 1),  # Standard
        "M2": (21, 10, 14, 2, 6, 5),   # Restricted
        "M3": (18, 8, 12, 2, 5, 9),    # Sacrifice
    },
    "Food Production": {
        "F1": (24, 34, 8, 4, 2, 1),  # Standard
        "F2": (15, 27, 5, 4, 2, 5),  # Restricted
        "F3": (10, 24, 5, 2, 1, 9),  # Sacrifice
    },
    "Engineering": {
        "E1": (26, 6, 6, 22, 10, 1),  # Standard
        "E2": (20, 5, 5, 18, 7, 5),   # Restricted
        "E3": (16, 4, 4, 14, 5, 9),   # Sacrifice
    },
}

DEPARTMENTS: Tuple[str, ...] = (
    "Life Support",
    "Medical",
    "Food Production",
    "Engineering",
)

MODE_KIND: Dict[str, str] = {
    "1": "Standard",
    "2": "Restricted",
    "3": "Sacrifice",
}

# The two known baseline (feasible) solutions, from the guide. They are
# provided for reference/tests only; the solver rediscovers them independently.
BASELINE_PATH_A: Dict[str, str] = {
    "Life Support": "L3",
    "Medical": "M2",
    "Food Production": "F2",
    "Engineering": "E2",
}
BASELINE_PATH_B: Dict[str, str] = {
    "Life Support": "L2",
    "Medical": "M2",
    "Food Production": "F2",
    "Engineering": "E3",
}

# Baseline policy limits (see guide section 02 / 04).
BASELINE_RISK_LIMIT = 24
CRISIS_OVERRIDE_RISK_LIMIT = 28
BASELINE_MAX_SACRIFICES = 1
CRISIS_OVERRIDE_MAX_SACRIFICES = 2

DEFAULT_RESOURCES: Dict[str, int] = {
    "power": 79,
    "water": 52,
    "oxygen": 59,
    "robot": 26,
    "bandwidth": 17,
}


@dataclass(frozen=True)
class Mode:
    """A single immutable operating mode package."""

    mode_id: str
    department: str
    kind: str
    power: int
    water: int
    oxygen: int
    robot: int
    bandwidth: int
    risk: int

    @property
    def is_sacrifice(self) -> bool:
        return self.kind == "Sacrifice"

    def as_vector(self) -> Dict[str, int]:
        return {
            "power": self.power,
            "water": self.water,
            "oxygen": self.oxygen,
            "robot": self.robot,
            "bandwidth": self.bandwidth,
        }

    def as_tuple(self) -> Tuple[int, int, int, int, int, int]:
        return (self.power, self.water, self.oxygen, self.robot, self.bandwidth, self.risk)

    def describe(self) -> str:
        return (
            f"{self.mode_id} {self.kind}: "
            f"P{self.power}/W{self.water}/O{self.oxygen}/R{self.robot}/B{self.bandwidth}, "
            f"risk {self.risk}"
        )


def _build_modes() -> Dict[str, Mode]:
    modes: Dict[str, Mode] = {}
    for dept, table in MODE_TABLE.items():
        for mode_id, values in table.items():
            power, water, oxygen, robot, bandwidth, risk = values
            modes[mode_id] = Mode(
                mode_id=mode_id,
                department=dept,
                kind=MODE_KIND[mode_id[1]],
                power=power,
                water=water,
                oxygen=oxygen,
                robot=robot,
                bandwidth=bandwidth,
                risk=risk,
            )
    return modes


MODES: Dict[str, Mode] = _build_modes()


def modes_for(department: str) -> Dict[str, Mode]:
    """Return the three modes for a department keyed by mode id."""
    return {m.mode_id: m for m in MODES.values() if m.department == department}


def mode(mode_id: str) -> Mode:
    return MODES[mode_id]


@dataclass
class SelectionSet:
    """One chosen mode per department (a candidate plan's mode selections)."""

    selections: Dict[str, str] = field(default_factory=dict)

    def totals(self) -> Dict[str, int]:
        totals = {k: 0 for k in RESOURCE_ORDER}
        for dept, mode_id in self.selections.items():
            m = MODES[mode_id]
            v = m.as_vector()
            for k in RESOURCE_ORDER:
                totals[k] += v[k]
        return totals

    def total_risk(self) -> int:
        return sum(MODES[mid].risk for mid in self.selections.values())

    def sacrifices(self) -> list[str]:
        return [dept for dept, mid in self.selections.items() if MODES[mid].is_sacrifice]

    def to_dict(self) -> Dict[str, str]:
        return dict(self.selections)
