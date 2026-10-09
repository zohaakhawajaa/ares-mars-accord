"""High-level engine tying agents, Commander, validator and storage together.

Usage:
    engine = AresEngine()
    engine.start()                         # baseline crisis
    engine.inject_event(event_dict)        # judge-injected emergency
    engine.export("exports")
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .agents import AgentCouncil
from .commander import Commander, NegotiationOutcome
from .config import settings
from .llm import LLMClient
from .models import (
    CommitmentStatus,
    CrisisEvent,
    Message,
    MessageType,
    Plan,
    PlanStatus,
    Role,
    ScenarioState,
)
from .packages import CRISIS_OVERRIDE_MAX_SACRIFICES, CRISIS_OVERRIDE_RISK_LIMIT, DEFAULT_RESOURCES, RESOURCE_ORDER
from .storage import export_all


def _search(raw: Dict[str, Any], names: List[str]):
    """Search a flat dict and its nested ``impact`` dict for the first matching key."""
    scopes = [raw]
    impact = raw.get("impact")
    if isinstance(impact, dict):
        scopes.append(impact)
    for scope in scopes:
        for name in names:
            if name in scope and scope[name] is not None:
                return scope[name]
    return None


def parse_event(raw: Dict[str, Any], base_resources: Dict[str, int]) -> CrisisEvent:
    """Turn an arbitrary judge event JSON into resource deltas.

    Supports percentage reductions (``power_reduction_percent``), fixed
    decreases (``power_reduction`` / ``water_decrease``), explicit
    ``resource_deltas`` and optional policy overrides. This makes the system
    robust to the judges' UNSEEN events.
    """
    if not isinstance(raw, dict):
        raw = {}
    deltas: Dict[str, int] = {}

    explicit = raw.get("resource_deltas")
    if isinstance(explicit, dict):
        for k, v in explicit.items():
            if k in RESOURCE_ORDER:
                try:
                    deltas[k] = deltas.get(k, 0) + int(v)
                except (TypeError, ValueError):
                    pass

    for k in RESOURCE_ORDER:
        pct = _search(raw, [f"{k}_reduction_percent", f"{k}_decrease_percent", f"{k}_loss_percent"])
        if pct is not None:
            try:
                delta = -abs(round(base_resources.get(k, 0) * float(pct) / 100.0))
                deltas[k] = deltas.get(k, 0) + delta
                continue
            except (TypeError, ValueError):
                pass
        decrease = _search(raw, [f"{k}_reduction", f"{k}_decrease", f"{k}_loss"])
        if decrease is not None:
            try:
                deltas[k] = deltas.get(k, 0) - abs(int(decrease))
            except (TypeError, ValueError):
                pass

    risk_limit = _search(raw, ["risk_limit", "risk_limit_override"])
    max_sacrifices = _search(raw, ["max_sacrifices", "allow_second_sacrifice"])
    if max_sacrifices is True:
        max_sacrifices = 2

    event = CrisisEvent(
        event_id=str(raw.get("event_id") or f"CRISIS_{int(time.time()) % 1000:03d}"),
        event_name=str(raw.get("event_name") or "Injected Emergency"),
        description=str(raw.get("description") or raw.get("message_to_commander") or ""),
        resource_deltas=deltas,
        risk_limit=int(risk_limit) if risk_limit is not None else None,
        max_sacrifices=int(max_sacrifices) if max_sacrifices is not None else None,
        requires_replan=bool(raw.get("requires_replan", True)),
        raw=raw,
    )
    return event


class AresEngine:
    def __init__(self, llm: Optional[LLMClient] = None, enabled: bool = True):
        self.llm = llm or LLMClient()
        self.state = ScenarioState()
        self.transcript: List[Message] = []
        self.council = AgentCouncil(self.llm, enabled=enabled, transcript=self.transcript)
        self.commander = Commander(self.council, self.state, self.transcript)
        self.outcomes: List[NegotiationOutcome] = []
        self.crisis_log: List[dict] = []
        self.final_plan: Optional[Plan] = None
        self.started = False

    # -- human-in-the-loop -------------------------------------------------
    def set_human_review(self, enabled: bool):
        """Opt in to a human approval gate for high-risk plans (risk > 20)."""
        self.commander.require_human_review = bool(enabled)
        return self.commander.require_human_review

    @property
    def human_review_pending(self) -> bool:
        return self.commander.pending_human_plan is not None

    def approve_as_human(self):
        ok, message = self.commander.approve_as_human()
        if ok and self.commander.current_plan is not None:
            self.final_plan = self.commander.current_plan
        return ok, message

    def reject_as_human(self, reason: str = ""):
        return self.commander.reject_as_human(reason)

    # -- lifecycle --------------------------------------------------------
    def start(self, resources: Optional[Dict[str, int]] = None,
              risk_limit: Optional[int] = None) -> NegotiationOutcome:
        if resources:
            self.state.resources = {k: int(resources.get(k, DEFAULT_RESOURCES[k])) for k in RESOURCE_ORDER}
        if risk_limit is not None:
            self.state.risk_limit = int(risk_limit)
        self.state.scenario_id = "INITIAL"
        self.state.status = "NEGOTIATING"
        self.started = True
        self.commander.override_allowed = False
        outcome = self.commander.run()
        self.outcomes.append(outcome)
        if outcome.status == "APPROVED":
            self.final_plan = outcome.plan
        return outcome

    def inject_event(self, raw: Dict[str, Any]) -> NegotiationOutcome:
        """Judge-injected emergency: update state, mark plans stale, renegotiate."""
        if not self.started:
            self.start()

        pre_resources = dict(self.state.resources)
        event = parse_event(raw, base_resources=pre_resources)
        event.injected_at_round = self.state.round

        # Apply resource deltas (never below zero).
        for k in RESOURCE_ORDER:
            if k in event.resource_deltas:
                self.state.resources[k] = max(0, self.state.resources.get(k, 0) + event.resource_deltas[k])

        # Optional policy overrides carried by the event.
        if event.risk_limit is not None:
            self.state.risk_limit = max(self.state.risk_limit, event.risk_limit)
        if event.max_sacrifices is not None:
            self.state.max_sacrifices = max(self.state.max_sacrifices, event.max_sacrifices)

        # 1) Record the event.
        self.state.events.append(event)
        self.state.scenario_id = event.event_id
        self.emit_system(
            MessageType.STATE,
            (
                f"EMERGENCY INJECTED [{event.event_id}] {event.event_name}: {event.description} "
                f"Resource deltas: {event.resource_deltas or 'none'}. "
                f"Council must reconvene. Crisis Override is now permitted."
            ),
            {"event": event.model_dump(mode="json"), "resources": dict(self.state.resources)},
        )

        # A newly injected emergency supersedes any plan still awaiting human
        # review: mark it stale so it can never be approved afterwards, and
        # clear the pending pointer so the human gate now refers to nothing.
        pending = self.commander.pending_human_plan
        if pending is not None:
            pending.status = PlanStatus.STALE
            self.commander.pending_human_plan = None
            self.commander.human_decision = None
            self.emit_system(
                MessageType.OBJECTION,
                (
                    f"Human review cancelled: plan v{pending.version} was awaiting sign-off "
                    "but the new emergency supersedes it. It is now STALE and will be "
                    "renegotiated with fresh votes."
                ),
            )

        # 2) Mark the old approved plan stale / invalid.
        stale_note = self._mark_plans_stale()

        # 3) Review commitments: expire old promises that must be renegotiated.
        expired = self._expire_commitments()

        # 4) Renegotiate with override permitted.
        self.commander.override_allowed = True
        self.state.round = 0
        self.state.status = "RENEGOTIATING"
        outcome = self.commander.run()
        self.outcomes.append(outcome)
        if outcome.status == "APPROVED":
            self.final_plan = outcome.plan

        self.crisis_log.append({
            "event": event.model_dump(mode="json"),
            "pre_event_resources": pre_resources,
            "post_event_resources": dict(self.state.resources),
            "stale_note": stale_note,
            "expired_commitments": expired,
            "outcome_status": outcome.status,
            "outcome_detail": outcome.detail,
            "rounds_used": outcome.rounds,
            "new_plan_version": outcome.plan.version if outcome.plan else None,
            "timestamp": time.time(),
        })
        return outcome

    # -- helpers ----------------------------------------------------------
    def emit_system(self, mtype: MessageType, content: str, payload: Optional[dict] = None):
        msg = Message(
            scenario_id=self.state.scenario_id,
            round=self.state.round,
            plan_version=self.state.plan_version,
            sender=Role.COMMANDER,
            type=mtype,
            content=content,
            payload=payload or {},
            engine="system",
        )
        self.transcript.append(msg)
        return msg

    def _mark_plans_stale(self) -> str:
        from . import validator

        notes = []
        for plan in self.commander.plans:
            if plan.status != PlanStatus.APPROVED:
                continue
            check = validator.validate_plan(
                selections=plan.selections,
                resources=self.state.resources,
                risk_limit=self.state.risk_limit,
                max_sacrifices=self.state.max_sacrifices,
                plan_version=plan.version,
                commitments=plan.commitments,
                require_votes=False,
            )
            if check.passed:
                plan.status = PlanStatus.STALE
                notes.append(f"v{plan.version} STALE (superseded by new event)")
            else:
                plan.status = PlanStatus.INVALID
                notes.append(f"v{plan.version} INVALID: " + "; ".join(check.failed_reasons()))
        if notes:
            self.emit_system(MessageType.OBJECTION, "Plan review: " + " | ".join(notes))
        return " | ".join(notes)

    def _expire_commitments(self) -> int:
        count = 0
        for c in self.commander.commitments:
            if c.is_accepted():
                c.status = CommitmentStatus.EXPIRED
                count += 1
        if count:
            self.emit_system(
                MessageType.INFO,
                f"Commitment review: {count} prior accepted return(s) expired and must be renegotiated for the new crisis.",
            )
        return count

    # -- query / export ---------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        plan = self.commander.current_plan
        available = self.state.resource_summary()
        used = plan.totals if plan else {k: 0 for k in RESOURCE_ORDER}
        reserve = {k: available.get(k, 0) - used.get(k, 0) for k in RESOURCE_ORDER}
        return {
            "provider": settings.describe(),
            "provider_key": settings.resolved_provider(),
            "scenario_id": self.state.scenario_id,
            "status": self.state.status,
            "round": self.state.round,
            "plan_version": self.state.plan_version,
            "plan": plan.to_dict() if plan else None,
            "available": available,
            "used": used,
            "reserve": reserve,
            "risk_limit": self.state.risk_limit,
            "max_sacrifices": self.state.max_sacrifices,
            "override_active": self.state.override_active,
            "human_review_required": self.commander.require_human_review,
            "human_review_pending": self.human_review_pending,
            "model_calls": {
                "total": len(getattr(self.llm, "call_log", [])),
                "ok": sum(1 for c in getattr(self.llm, "call_log", []) if c.get("ok")),
                "failed": sum(1 for c in getattr(self.llm, "call_log", []) if not c.get("ok")),
                "last_error": getattr(self.llm, "last_error_safe", "") or "",
            },
            "events": [e.model_dump(mode="json") for e in self.state.events],
        }

    def export(self, export_dir: str = "exports") -> Dict[str, str]:
        return export_all(
            export_dir=export_dir,
            messages=self.transcript,
            plans=self.commander.plans,
            state=self.state,
            crisis_log=self.crisis_log,
            final_plan=self.final_plan,
            llm_log=getattr(self.llm, "call_log", []),
        )

    def reset(self):
        llm = self.llm
        self.__init__(llm=llm)
