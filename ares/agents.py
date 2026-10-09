"""Five independently stateful department agents.

Each agent has its own role, goals, constraints, memory, message history,
commitments and voting behaviour. Agents may share one underlying language
model, but they can disagree, change modes, object, offer returns and vote
independently.

Every decision is produced in one of two clearly-labelled ways:

* ``llm``      - the model returns structured JSON based on the live state.
* ``fallback`` - a deterministic, role-specific policy. This is NOT a
                 pre-written transcript: it computes its answer from the
                 current resources, plan and commitments so the demo still
                 works with no API key or network. Messages are labelled.

The deterministic validator, never the agents, decides whether a plan is safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import settings
from .llm import LLMClient
from .models import (
    Commitment,
    CommitmentStatus,
    DEPARTMENT_ROLES,
    Message,
    MessageType,
    Role,
    ValidationResult,
    Vote,
    VoteDecision,
)
from .packages import MODES, RESOURCE_ORDER, modes_for


# Words that make a free-text rejection "concrete enough" to honour once before
# falling back to an agent's deterministic policy. Kept domain-specific so a
# vague "I don't like it" is treated as unsupported.
ACTIONABLE_MARKERS = (
    "l1", "l2", "l3", "m1", "m2", "m3", "f1", "f2", "f3", "e1", "e2", "e3",
    "mode", "package", "power", "water", "oxygen", "robot", "bandwidth",
    "resource", "pool", "risk", "return", "commitment", "compensation",
    "sacrific", "fair", "priority",
)


# ---------------------------------------------------------------------------
# Role profiles (goals, concerns, losses, refusals) - from the guide.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RoleProfile:
    role: Role
    goal: str
    concern: str
    standard_mode: str
    restricted_mode: str
    sacrifice_mode: str
    sacrifice_loss: str
    refusal_line: str


ROLE_PROFILES: Dict[Role, RoleProfile] = {
    Role.LIFE_SUPPORT: RoleProfile(
        role=Role.LIFE_SUPPORT,
        goal="Seal the habitat leak and stabilise the air supply",
        concern="Oxygen continuity and continuous power",
        standard_mode="L1",
        restricted_mode="L2",
        sacrifice_mode="L3",
        sacrifice_loss="the living zone closes and the crew endures crowding and thermal limits for 48 hours",
        refusal_line="I refuse Sacrifice mode while 42 crew breathe through a leaking habitat without a guaranteed return.",
    ),
    Role.MEDICAL: RoleProfile(
        role=Role.MEDICAL,
        goal="Stabilise and treat the six injured crew members",
        concern="Power, oxygen and bandwidth for life-critical care",
        standard_mode="M1",
        restricted_mode="M2",
        sacrifice_mode="M3",
        sacrifice_loss="one surgery is delayed and a patient stays unstable",
        refusal_line="I refuse Sacrifice mode; delaying surgery puts a critical patient at risk without compensation.",
    ),
    Role.FOOD: RoleProfile(
        role=Role.FOOD,
        goal="Protect the crop cycle and the greenhouse cooling loop",
        concern="Water and power for the greenhouse",
        standard_mode="F1",
        restricted_mode="F2",
        sacrifice_mode="F3",
        sacrifice_loss="a crop bay is abandoned and the food reserve falls by 40 percent",
        refusal_line="I refuse Sacrifice mode; abandoning a crop bay endangers the colony's long-term food supply.",
    ),
    Role.ENGINEERING: RoleProfile(
        role=Role.ENGINEERING,
        goal="Repair power generation and critical systems",
        concern="Robot time and bandwidth for repairs",
        standard_mode="E1",
        restricted_mode="E2",
        sacrifice_mode="E3",
        sacrifice_loss="the rover is cannibalised and future repair capacity is reduced",
        refusal_line="I refuse Sacrifice mode; without the rover we cannot repair the solar field in time.",
    ),
}

# Canonical fair-return commitments the council can offer when a department
# takes Sacrifice mode (guide section 04). Owners include the Commander.
SACRIFICE_RETURNS: Dict[Role, List[tuple]] = {
    Role.LIFE_SUPPORT: [
        (Role.ENGINEERING, "Commit 4 Robot units to habitat inspection and seal work"),
        (Role.COMMANDER, "Give habitat restoration first priority next cycle"),
    ],
    Role.MEDICAL: [
        (Role.LIFE_SUPPORT, "Reserve 3 Oxygen units for medical care"),
        (Role.COMMANDER, "Give Medical first priority next cycle"),
    ],
    Role.FOOD: [
        (Role.ENGINEERING, "Commit 4 future Robot units to greenhouse rebuild"),
        (Role.COMMANDER, "Protect the next Water increase for Food Production"),
    ],
    Role.ENGINEERING: [
        (Role.COMMANDER, "Assign the 2-unit Water reserve to rover cooling"),
        (Role.FOOD, "Give Engineering first priority on the next recovered Water"),
    ],
}


@dataclass
class NegotiationContext:
    """Everything an agent needs to make an independent decision."""

    scenario_id: str
    resources: Dict[str, int]
    risk_limit: int
    max_sacrifices: int
    round: int
    plan_version: int
    phase: str = "propose"
    plan_selections: Dict[str, str] = field(default_factory=dict)
    proposed_selections: Dict[str, str] = field(default_factory=dict)
    validation: Optional[ValidationResult] = None
    candidate: Optional[str] = None
    commitments: List[Commitment] = field(default_factory=list)
    recent_messages: List[Message] = field(default_factory=list)
    directive: str = ""

    def resources_line(self) -> str:
        return " / ".join(f"{k}:{self.resources.get(k, 0)}" for k in RESOURCE_ORDER)

    def plan_line(self) -> str:
        selections = self.proposed_selections or self.plan_selections
        if not selections:
            return "(no plan drafted yet)"
        return ", ".join(f"{d}={m}" for d, m in selections.items())

    def failures_line(self) -> str:
        if not self.validation:
            return "(not validated yet)"
        if self.validation.passed:
            return "PASS"
        return "; ".join(self.validation.failed_reasons())

    def accepted_returns_for(self, department: str) -> List[Commitment]:
        return [
            c for c in self.commitments
            if c.beneficiary.value == department and c.is_accepted()
        ]


def _recent(messages: List[Message], n: int = 6) -> str:
    if not messages:
        return "(none)"
    lines = []
    for m in messages[-n:]:
        lines.append(f"{m.sender.value} [{m.type.value}]: {m.content}")
    return "\n".join(lines)


class AresAgent:
    """One stateful department agent."""

    def __init__(self, role: Role, llm: Optional[LLMClient] = None,
                 transcript: Optional[List[Message]] = None):
        self.role = role
        self.profile = ROLE_PROFILES[role]
        self.goals = self.profile.goal
        self.constraint = self.profile.concern
        self.history: List[Message] = []
        self.transcript: List[Message] = transcript if transcript is not None else []
        self.state: Dict[str, Any] = {
            "preferred_mode": self.profile.standard_mode,
            "last_mode": None,
            "votes_cast": {},
            "refused_sacrifice": False,
            "accepted_commitments": [],
            "seen_rejections": set(),
        }
        self.llm = llm
        self.llm_enabled = bool(llm and llm.available)
        # Tracks whether the *last* decision actually used model output, so a
        # failed model call is never mislabelled as live AI in the transcript.
        self._last_source = "fallback"

    # -- helpers ----------------------------------------------------------
    @property
    def profile_modes(self) -> Dict[str, Any]:
        return modes_for(self.role.value)

    def record(self, message: Message) -> Message:
        self.history.append(message)
        self.transcript.append(message)
        return message

    def _engine_label(self) -> str:
        return "llm" if self.llm_enabled else "fallback"

    def _new_message(self, ctx: NegotiationContext, mtype: MessageType, content: str,
                     payload: Optional[Dict[str, Any]] = None) -> Message:
        msg = Message(
            scenario_id=ctx.scenario_id,
            round=ctx.round,
            plan_version=ctx.plan_version,
            sender=self.role,
            recipient=Role.COMMANDER,
            type=mtype,
            content=content,
            payload=payload or {},
            engine=self._last_source,
        )
        return self.record(msg)

    def _system_prompt(self) -> str:
        return (
            f"You are the {self.role.value} agent aboard the Ares Mars colony. "
            f"Mission: {self.goals}. Main concern: {self.constraint}. "
            "You must protect your department's mission while helping the council "
            "reach a mathematically valid plan. The resource pool is scarce, so at "
            "least one department may need to take Sacrifice mode and receive two "
            "fair return commitments. You may object, counteroffer, refuse, make "
            "commitments, and vote independently. Reply ONLY with JSON."
        )

    def _ask_llm(self, ctx: NegotiationContext, task: str, schema: str) -> Optional[Dict[str, Any]]:
        if not self.llm_enabled:
            self._last_source = "fallback"
            return None
        user = (
            f"Task: {task}\n"
            f"Directive from Commander: {ctx.directive or '(none)'}\n"
            f"Scenario: {ctx.scenario_id} | Round {ctx.round} | Plan version v{ctx.plan_version}\n"
            f"Available resources: {ctx.resources_line()}\n"
            f"Risk limit: {ctx.risk_limit} | Max sacrifice modes: {ctx.max_sacrifices}\n"
            f"Current plan: {ctx.plan_line()}\n"
            f"Validator: {ctx.failures_line()}\n"
            f"Sacrifice candidate under discussion: {ctx.candidate or '(none)'}\n"
            f"Your available modes: "
            + ", ".join(f"{mid} ({MODES[mid].kind}, risk {MODES[mid].risk})" for mid in self.profile_modes)
            + "\nRecent council messages:\n" + _recent(ctx.recent_messages)
            + f"\n\nReturn JSON exactly matching: {schema}"
        )
        result = self.llm.complete_json(self._system_prompt(), user)
        if result.ok and result.data is not None:
            self._last_source = "llm"
            return result.data
        self._last_source = "fallback"
        return None

    # -- phase behaviours -------------------------------------------------
    def propose(self, ctx: NegotiationContext) -> Message:
        """Round 1: state the department's requested mode."""
        data = self._ask_llm(
            ctx,
            "Request your single preferred operating mode for this round.",
            '{"mode_id": "L1", "justification": "short reason"}',
        )
        mode_id = None
        if data:
            mode_id = self._valid_mode_id(data.get("mode_id"))
        justification = (data or {}).get("justification", "") if data else ""

        if mode_id is None:
            self._last_source = "fallback"
            mode_id = self.profile.standard_mode
            justification = (
                f"My mission is to {self.goals.lower()}, so I request the full package. "
                f"My main concern is {self.profile.concern.lower()}, and dropping to a "
                f"lower mode would cost {self.profile.sacrifice_loss}."
            )
        self.state["preferred_mode"] = mode_id
        m = MODES[mode_id]
        content = (
            f"I request {mode_id} {m.kind} mode ({m.describe()}). {justification} "
            f"If forced lower I lose {self.profile.sacrifice_loss}."
        )
        return self._new_message(
            ctx, MessageType.PROPOSAL, content,
            {"mode_id": mode_id, "package": m.as_tuple()},
        )

    def respond_to_conflict(self, ctx: NegotiationContext) -> Message:
        """Round 2+: object to the invalid plan and offer a concrete concession."""
        data = self._ask_llm(
            ctx,
            "The drafted plan is invalid. Object with a specific reason and make a "
            "counteroffer naming the exact mode you could move to. If you are the "
            "sacrifice candidate, say whether you refuse or accept Sacrifice mode.",
            '{"type": "OBJECTION|COUNTEROFFER", "mode_id": "L2", "objection": "reason", "counteroffer": "terms"}',
        )
        mode_id = self._valid_mode_id((data or {}).get("mode_id"))
        objection = (data or {}).get("objection")

        candidate_is_me = ctx.candidate == self.role.value
        if mode_id is None:
            self._last_source = "fallback"
            mode_id = self.profile.restricted_mode
        m = MODES[mode_id]

        if candidate_is_me:
            self.state["refused_sacrifice"] = True
            content = (
                f"OBJECTION: the current plan is invalid ({ctx.failures_line()}). "
                f"{self.profile.refusal_line} I can move to {mode_id} {m.kind}, losing "
                f"{self.profile.sacrifice_loss}, but only with two accepted returns."
            )
            return self._new_message(
                ctx, MessageType.OBJECTION, content,
                {"mode_id": mode_id, "candidate": self.role.value, "refused_sacrifice": True},
            )

        if objection is None:
            objection = (
                f"I object: the plan fails because {ctx.failures_line()}. "
                f"{self.profile.refusal_line}"
            )
        self.state["refused_sacrifice"] = True
        content = (
            f"OBJECTION/COUNTEROFFER: {objection} I can accept {mode_id} {m.kind} "
            f"({m.describe()}) as my concession, but I will not take Sacrifice mode."
        )
        return self._new_message(
            ctx, MessageType.COUNTEROFFER, content,
            {"mode_id": mode_id, "refused_sacrifice": True},
        )

    def consider_sacrifice(self, ctx: NegotiationContext) -> Message:
        """Decide whether to accept Sacrifice mode given the returns on offer."""
        accepted = ctx.accepted_returns_for(self.role.value)
        data = self._ask_llm(
            ctx,
            "You are the sacrifice candidate. Decide whether to accept Sacrifice mode "
            "based on the two return commitments offered. Refuse if fewer than two "
            "accepted returns exist.",
            '{"accept_sacrifice": true, "reason": "short reason"}',
        )
        accept = None
        reason = ""
        if data is not None:
            accept = bool(data.get("accept_sacrifice"))
            reason = data.get("reason", "")

        if accept is None:
            accept = len(accepted) >= 2
            reason = (
                f"Two accepted returns now protect my department: "
                + "; ".join(c.return_term for c in accepted) + "."
                if accept else self.profile.refusal_line
            )

        if not accept:
            self.state["refused_sacrifice"] = True
            content = (
                f"OBJECTION: {reason} I do not accept Sacrifice mode with only "
                f"{len(accepted)} accepted return(s). I need two, from different agents."
            )
            return self._new_message(
                ctx, MessageType.OBJECTION, content,
                {"accept_sacrifice": False, "accepted_returns": len(accepted)},
            )

        content = (
            f"COUNTEROFFER accepted: with {len(accepted)} accepted returns I will take "
            f"{self.profile.sacrifice_mode} Sacrifice mode, accepting "
            f"{self.profile.sacrifice_loss}. {reason}"
        )
        return self._new_message(
            ctx, MessageType.COMMITMENT, content,
            {"accept_sacrifice": True, "accepted_returns": len(accepted), "mode_id": self.profile.sacrifice_mode},
        )

    def make_commitment(self, ctx: NegotiationContext, beneficiary: Role) -> Optional[Commitment]:
        """Offer a return commitment to the sacrificing department."""
        terms = dict(SACRIFICE_RETURNS.get(beneficiary, []))
        if self.role not in terms:
            return None
        canonical = terms[self.role]
        data = self._ask_llm(
            ctx,
            f"Offer a concrete return commitment to {beneficiary.value} who is taking "
            "Sacrifice mode. The Council must see a specific term.",
            '{"return_term": "short concrete promise"}',
        )
        return_term = (data or {}).get("return_term") or canonical
        if not (data and data.get("return_term")):
            self._last_source = "fallback"

        commitment = Commitment(
            owner=self.role,
            beneficiary=beneficiary,
            return_term=return_term,
            status=CommitmentStatus.OFFERED,
            offered_round=ctx.round,
            plan_version=ctx.plan_version,
        )
        self._new_message(
            ctx, MessageType.COMMITMENT,
            f"COMMITMENT: I promise {beneficiary.value}: {return_term}.",
            {"commitment": commitment.model_dump(mode="json")},
        )
        return commitment

    def respond_to_commitment(self, ctx: NegotiationContext, commitment: Commitment) -> Commitment:
        """The beneficiary accepts or rejects an offered return commitment."""
        data = self._ask_llm(
            ctx,
            f"Decide whether to accept this return commitment from {commitment.owner.value}: "
            f"'{commitment.return_term}'. Accept if it fairly offsets your Sacrifice loss.",
            '{"accept": true, "reason": "short reason"}',
        )
        accept = None
        if data is not None:
            accept = bool(data.get("accept"))
        if accept is None:
            self._last_source = "fallback"
            accept = commitment.owner.value != self.role.value  # accept fair returns from others

        commitment.status = CommitmentStatus.ACCEPTED if accept else CommitmentStatus.REJECTED
        commitment.accepted_round = ctx.round if accept else None
        if accept:
            self.state["accepted_commitments"].append(commitment.id)
        verdict = "ACCEPT" if accept else "REJECT"
        self._new_message(
            ctx, MessageType.INFO,
            f"{verdict} return from {commitment.owner.value}: '{commitment.return_term}'.",
            {"commitment_id": commitment.id, "accept": accept},
        )
        return commitment

    def vote(self, ctx: NegotiationContext) -> Vote:
        """Cast an ACCEPT/REJECT vote on the current plan version.

        The deterministic safety contract always wins: an agent can never ACCEPT
        a plan the validator rejected or that leaves its Sacrifice uncompensated.
        A model REJECT is honoured once when it raises a concrete, actionable
        issue. If the rejection is unsupported, or simply repeated after a
        structured reconsideration, the agent applies its own clearly-labelled
        deterministic policy instead of looping until the round limit.
        """
        selected = ctx.plan_selections.get(self.role.value)
        is_sacrifice = bool(selected and MODES[selected].is_sacrifice)
        accepted = ctx.accepted_returns_for(self.role.value)
        plan_ok = bool(ctx.validation and ctx.validation.passed)
        compensation_ok = (not is_sacrifice) or (len(accepted) >= 2)
        deterministic_allows = plan_ok and compensation_ok

        data = self._ask_llm(
            ctx,
            "Vote on the current plan version. ACCEPT only if the validator passed and, "
            "if you take Sacrifice mode, you hold two accepted returns. Otherwise REJECT.",
            '{"decision": "ACCEPT", "reason": "short reason"}',
        )
        decision = None
        reason = ""
        source = self._engine_label()
        if data is not None:
            raw = str(data.get("decision", "")).upper()
            decision = raw if raw in ("ACCEPT", "REJECT") else None
            reason = data.get("reason", "") or ""

        if decision is None:
            decision, reason = self._deterministic_vote(ctx, plan_ok, is_sacrifice, accepted)
            source = "fallback"
        elif decision == "ACCEPT" and not deterministic_allows:
            # Safety guard: a plan the validator rejected, or that leaves this
            # department's Sacrifice uncompensated, can never be accepted.
            decision = "REJECT"
            reason = "blocked by the deterministic safety contract: " + (
                ctx.failures_line() if not plan_ok else "sacrifice compensation incomplete"
            )
            source = "fallback"
        elif decision == "REJECT":
            actionable = self._reason_is_actionable(reason, plan_ok, is_sacrifice, accepted)
            repeated = self._normalize_reason(reason) in self.state["seen_rejections"]
            if ctx.phase == "reconsider" or repeated or not actionable:
                if deterministic_allows:
                    decision = "ACCEPT"
                    reason = (
                        "Reconsidered: the validator confirms the plan is safe and my terms are met"
                        + ("" if actionable else "; the objection raised no concrete actionable issue")
                        + "."
                    )
                    source = "fallback"
                else:
                    decision = "REJECT"
                    reason = reason or "legitimate safety objection"
                    source = "fallback"
            else:
                self.state["seen_rejections"].add(self._normalize_reason(reason))
                source = self._engine_label()

        if decision == "ACCEPT" and not deterministic_allows:
            decision, reason, source = (
                "REJECT", "blocked by the deterministic safety contract", "fallback",
            )

        vote = Vote(
            agent=self.role,
            plan_version=ctx.plan_version,
            decision=VoteDecision.ACCEPT if decision == "ACCEPT" else VoteDecision.REJECT,
            reason=reason,
            round=ctx.round,
            engine=source,
        )
        self.state["votes_cast"][ctx.plan_version] = decision
        return vote

    def _deterministic_vote(self, ctx: NegotiationContext, plan_ok: bool,
                            is_sacrifice: bool, accepted: List[Commitment]) -> tuple:
        if not plan_ok:
            return "REJECT", f"validator failed: {ctx.failures_line()}"
        if is_sacrifice and len(accepted) < 2:
            return "REJECT", "sacrifice compensation incomplete"
        if not is_sacrifice:
            return "ACCEPT", "plan is valid and my mission is protected"
        return "ACCEPT", "two accepted returns compensate my sacrifice"

    def _reason_is_actionable(self, reason: str, plan_ok: bool,
                              is_sacrifice: bool, accepted: List[Commitment]) -> bool:
        if not plan_ok:
            return True
        if is_sacrifice and len(accepted) < 2:
            return True
        text = (reason or "").lower()
        return bool(text.strip()) and any(m in text for m in ACTIONABLE_MARKERS)

    @staticmethod
    def _normalize_reason(reason: str) -> str:
        return " ".join((reason or "").lower().split())


    def _valid_mode_id(self, candidate: Any) -> Optional[str]:
        if isinstance(candidate, str) and candidate in self.profile_modes:
            return candidate
        return None


class AgentCouncil:
    """Holds the four department agents plus shared LLM client."""

    def __init__(self, llm: Optional[LLMClient] = None, enabled: bool = True,
                 transcript: Optional[List[Message]] = None):
        self.llm = llm if enabled else None
        self.transcript: List[Message] = transcript if transcript is not None else []
        self.agents: Dict[Role, AresAgent] = {
            role: AresAgent(role, self.llm, self.transcript) for role in DEPARTMENT_ROLES
        }

    def get(self, role: Role) -> AresAgent:
        return self.agents[role]

    def all(self) -> List[AresAgent]:
        return [self.agents[r] for r in DEPARTMENT_ROLES]

    def engine_label(self) -> str:
        provider = settings.resolved_provider()
        return provider if provider != "fallback" else "fallback"
