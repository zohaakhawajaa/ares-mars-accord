# Output schema & rubric — honest notes

This document records two discrepancies between the provided starter files and
the actual scenario, and how ARES Accord handles them **without inventing data**.

## 1. `output_schema.json` vs. the real five-resource model

### What the starter file says
`output_schema.json` (repository root) is a **single per-agent record**:

```json
{
  "round": 1,
  "agent_id": "MEDICAL_OFFICER",
  "proposal": { "oxygen_units": 25, "water_liters": 80, "power_kwh": 40, "food_rations": 12, "justification": "..." },
  "vote": "APPROVE",
  "risk_score": 14,
  "flag_human_review": false
}
```

### How that differs from the challenge
- The scenario (`docs/CHALLENGE_README_ORIGINAL.md`, the guide PDF, and
  `RULES.md`) defines **five** usable resources: **Power, Water, Oxygen,
  Robot Time, Bandwidth**.
- `output_schema.json` includes **`food_rations`**, which is **not** one of those
  five resources, and omits **robot_time** and **bandwidth**. It also describes
  one **agent's** request rather than the council's final allocation across the
  four departments.

### What ARES Accord does (no fictional resources)
- The system models the **real five resources only**. `food_rations` is **never**
  introduced into the resource pool or the validator.
- `final_allocation.json` therefore contains two views:
  - **`allocation`** — the authoritative record: departments, selected modes,
    real totals (`power/water/oxygen/robot/bandwidth`), risk, sacrifices,
    commitments, validation and reserve.
  - **`output_schema`** — a convenience record shaped like the starter schema
    (`round`, `agent_id`, `proposal`, `vote`, `risk_score`, `flag_human_review`)
    but populated with the **real** totals. It maps `robot_time` and `bandwidth`
    honestly and does **not** emit `food_rations`.
- See `ares/storage.py::plan_to_output_schema` and the assertions in
  `tests/test_reliability.py::test_exports_exist_and_reflect_live_state`.

**Status:** documented divergence, not a defect; no fictional resource added.

## 2. Missing `judges_scoring_rubric.md`

`docs/CHALLENGE_README_ORIGINAL.md` lists a **100-point** `judges_scoring_rubric.md`
as part of the starting materials. A recursive search of this repository finds
**no such file** (and no `*rubric*` file of any kind):

```
Get-ChildItem -Recurse -File | Where-Object { $_.Name -match 'rubric' }   # -> (nothing)
```

The original README also references `ARES_Accord_24_Hour_Hackathon_Challenge_v2.2.pdf`;
the repository instead contains
`ARES_ACCORD_24_HOUR_VIRTUAL_CHALLENGE_LOOPVERSE_3.0.pdf`.

**Decision:** because the original rubric could not be found, we did **not**
invent scoring criteria or a point breakdown. If the organisers supply the real
rubric, it should be added to this repository and the demo/submission materials
reviewed against it.

## 3. What we optimise for instead

Absent the rubric, the implementation targets the **explicit requirements** in
the guide, `RULES.md` and `submission_checklist.md`, and each is covered by an
automated test or an independent audit check (see `README.md` and the final
report). The `submission_checklist.md` items are all satisfied except the
externally produced demo video and team declaration.
