"""Persistence helpers: JSON / CSV exports of the transcript and final plan."""

from __future__ import annotations

import csv
import json
import os
from typing import Iterable, List

from .models import Message, Plan, ScenarioState


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def write_json(path: str, data) -> str:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return path


def write_transcript_csv(path: str, messages: Iterable[Message]) -> str:
    ensure_dir(os.path.dirname(path) or ".")
    rows = [m.to_row() for m in messages]
    fieldnames = ["timestamp", "scenario", "round", "plan_version", "sender",
                  "recipient", "type", "engine", "content"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def plan_to_output_schema(plan: Plan, state: ScenarioState) -> dict:
    """Final allocation export, plus the mandatory output_schema-style view."""
    from .packages import MODES

    departments = []
    for dept, mode_id in plan.selections.items():
        m = MODES[mode_id]
        departments.append({
            "department": dept,
            "mode_id": mode_id,
            "kind": m.kind,
            "power": m.power,
            "water": m.water,
            "oxygen": m.oxygen,
            "robot_time": m.robot,
            "bandwidth": m.bandwidth,
            "risk": m.risk,
        })

    votes = {}
    if plan.validation:
        for c in plan.validation.checks:
            votes[c.name] = c.passed

    allocation = {
        "scenario_id": plan.scenario_id,
        "plan_version": plan.version,
        "status": plan.status.value,
        "selections": plan.selections,
        "departments": departments,
        "totals": plan.totals,
        "risk": plan.risk,
        "sacrifices": plan.sacrifices,
        "available_resources": state.resource_summary(),
        "reserve": {k: state.resources.get(k, 0) - plan.totals.get(k, 0) for k in state.resources},
        "commitments": [c.to_row() for c in plan.commitments],
        "validation": plan.validation.model_dump(mode="json") if plan.validation else None,
        "override_active": state.override_active,
        "risk_limit": state.risk_limit,
        "max_sacrifices": state.max_sacrifices,
    }

    # output_schema.json-compatible single record for convenience.
    output_record = {
        "round": state.round,
        "agent_id": "COMMANDER",
        "proposal": {
            "power_kwh": plan.totals.get("power"),
            "water_liters": plan.totals.get("water"),
            "oxygen_units": plan.totals.get("oxygen"),
            "robot_time": plan.totals.get("robot"),
            "bandwidth": plan.totals.get("bandwidth"),
            "justification": "Validated council allocation for " + plan.scenario_id,
        },
        "vote": "APPROVE" if plan.status.value == "APPROVED" else plan.status.value,
        "risk_score": plan.risk,
        "flag_human_review": plan.risk > 20,
    }

    return {"allocation": allocation, "output_schema": output_record}


def export_all(export_dir: str, messages: List[Message], plans: List[Plan],
               state: ScenarioState, crisis_log: List[dict],
               final_plan: Plan | None,
               llm_log: Iterable[dict] | None = None) -> dict:
    ensure_dir(export_dir)
    paths = {}
    paths["council_transcript.json"] = write_json(
        os.path.join(export_dir, "council_transcript.json"),
        [m.model_dump(mode="json") for m in messages],
    )
    paths["council_transcript.csv"] = write_transcript_csv(
        os.path.join(export_dir, "council_transcript.csv"), messages
    )
    paths["llm_operations_log.json"] = write_json(
        os.path.join(export_dir, "llm_operations_log.json"),
        list(llm_log or []),
    )
    paths["plans_history.json"] = write_json(
        os.path.join(export_dir, "plans_history.json"),
        [p.to_dict() for p in plans],
    )
    paths["crisis_handling_log.json"] = write_json(
        os.path.join(export_dir, "crisis_handling_log.json"), crisis_log
    )
    if final_plan is not None:
        paths["final_allocation.json"] = write_json(
            os.path.join(export_dir, "final_allocation.json"),
            plan_to_output_schema(final_plan, state),
        )
    return paths
