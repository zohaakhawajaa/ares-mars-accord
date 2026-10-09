"""Structured records shared across the ARES Accord system.

Pydantic models are used so every record can be validated and exported to
JSON/CSV deterministically. Nothing here talks to a language model.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .packages import (
    BASELINE_MAX_SACRIFICES,
    BASELINE_RISK_LIMIT,
    DEFAULT_RESOURCES,
    RESOURCE_ORDER,
)


class Role(str, Enum):
    COMMANDER = "Commander"
    LIFE_SUPPORT = "Life Support"
    MEDICAL = "Medical"
    FOOD = "Food Production"
    ENGINEERING = "Engineering"


DEPARTMENT_ROLES: List[Role] = [
    Role.LIFE_SUPPORT,
    Role.MEDICAL,
    Role.FOOD,
    Role.ENGINEERING,
]


class MessageType(str, Enum):
    STATE = "STATE"
    PROPOSAL = "PROPOSAL"
    OBJECTION = "OBJECTION"
    COUNTEROFFER = "COUNTEROFFER"
    COMMITMENT = "COMMITMENT"
    VOTE = "VOTE"
    APPROVAL = "APPROVAL"
    INFEASIBLE = "INFEASIBLE"
    INFO = "INFO"


class VoteDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class PlanStatus(str, Enum):
    DRAFT = "DRAFT"
    REJECTED = "REJECTED"
    FEASIBLE = "FEASIBLE"
    APPROVED = "APPROVED"
    STALE = "STALE"
    INVALID = "INVALID"
    INFEASIBLE = "INFEASIBLE"


class CommitmentStatus(str, Enum):
    OFFERED = "OFFERED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _now() -> float:
    return time.time()


def engine_badge(engine: str) -> str:
    """Short, honest provenance badge for a transcript message.

    Guarantees that deterministic fallback output is never presented as live
    AI output, and that system/Commander messages are not mislabelled either.
    """
    return {
        "llm": "🟢 LIVE AI",
        "fallback": "🔴 FALLBACK",
        "system": "⚙️ SYSTEM",
    }.get(engine, "⚙️ SYSTEM")


class Message(BaseModel):
    """A single exchange between agents, visible in the council transcript."""

    id: str = Field(default_factory=lambda: _uid("msg"))
    timestamp: float = Field(default_factory=_now)
    scenario_id: str = "INITIAL"
    round: int = 0
    plan_version: int = 0
    sender: Role
    recipient: Optional[Role] = None
    type: MessageType
    content: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    engine: str = "system"  # "llm", "fallback", or "system"

    def label(self) -> str:
        target = self.recipient.value if self.recipient else "Council"
        return f"[R{self.round} v{self.plan_version}] {self.sender.value} -> {target}"

    def to_row(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "scenario": self.scenario_id,
            "round": self.round,
            "plan_version": self.plan_version,
            "sender": self.sender.value,
            "recipient": self.recipient.value if self.recipient else "Council",
            "type": self.type.value,
            "engine": self.engine,
            "content": self.content,
        }


class Commitment(BaseModel):
    """A promised return for a department taking Sacrifice mode."""

    id: str = Field(default_factory=lambda: _uid("cmt"))
    owner: Role
    beneficiary: Role
    return_term: str
    status: CommitmentStatus = CommitmentStatus.OFFERED
    offered_round: int = 0
    accepted_round: Optional[int] = None
    expiry: str = "next cycle"
    plan_version: int = 0

    def is_accepted(self) -> bool:
        return self.status == CommitmentStatus.ACCEPTED

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "owner": self.owner.value,
            "beneficiary": self.beneficiary.value,
            "return_term": self.return_term,
            "status": self.status.value,
            "offered_round": self.offered_round,
            "accepted_round": self.accepted_round,
            "expiry": self.expiry,
            "plan_version": self.plan_version,
        }


class Vote(BaseModel):
    agent: Role
    plan_version: int
    decision: VoteDecision
    reason: str = ""
    round: int = 0
    engine: str = "system"

    def to_row(self) -> Dict[str, Any]:
        return {
            "agent": self.agent.value,
            "plan_version": self.plan_version,
            "decision": self.decision.value,
            "reason": self.reason,
            "round": self.round,
            "engine": self.engine,
        }


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    reason: str


class ValidationResult(BaseModel):
    passed: bool
    plan_version: int
    checks: List[ValidationCheck] = Field(default_factory=list)
    totals: Dict[str, int] = Field(default_factory=dict)
    risk: int = 0
    sacrifices: List[str] = Field(default_factory=list)

    def failed_reasons(self) -> List[str]:
        return [f"{c.name}: {c.reason}" for c in self.checks if not c.passed]


class Plan(BaseModel):
    version: int
    scenario_id: str = "INITIAL"
    selections: Dict[str, str] = Field(default_factory=dict)
    commitments: List[Commitment] = Field(default_factory=list)
    status: PlanStatus = PlanStatus.DRAFT
    created_round: int = 0
    totals: Dict[str, int] = Field(default_factory=dict)
    risk: int = 0
    sacrifices: List[str] = Field(default_factory=list)
    validation: Optional[ValidationResult] = None
    note: str = ""

    def accepted_commitments_for(self, department: str) -> List[Commitment]:
        return [
            c
            for c in self.commitments
            if c.beneficiary.value == department and c.is_accepted()
        ]

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class CrisisEvent(BaseModel):
    event_id: str
    event_name: str
    description: str = ""
    resource_deltas: Dict[str, int] = Field(default_factory=dict)
    risk_limit: Optional[int] = None
    max_sacrifices: Optional[int] = None
    requires_replan: bool = True
    raw: Dict[str, Any] = Field(default_factory=dict)
    injected_at_round: int = 0


class ScenarioState(BaseModel):
    scenario_id: str = "INITIAL"
    resources: Dict[str, int] = Field(default_factory=lambda: dict(DEFAULT_RESOURCES))
    risk_limit: int = BASELINE_RISK_LIMIT
    max_sacrifices: int = BASELINE_MAX_SACRIFICES
    round: int = 0
    max_rounds: int = 8
    plan_version: int = 0
    override_active: bool = False
    events: List[CrisisEvent] = Field(default_factory=list)
    status: str = "IDLE"

    def resource_summary(self) -> Dict[str, int]:
        return {k: self.resources.get(k, 0) for k in RESOURCE_ORDER}
