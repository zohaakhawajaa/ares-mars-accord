"""Commander / orchestrator.

The Commander owns the negotiation protocol: it publishes the crisis, opens
rounds, detects conflicts, drafts numbered plan versions, asks the deterministic
validator for the verdict, collects return commitments and votes, and only
approves when every rule is satisfied.

It CANNOT bypass the validator, the risk limits, the return agreements or the
votes. All arithmetic is delegated to :mod:`ares.solver` and
:mod:`ares.validator`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import solver, validator
from .agents import AgentCouncil, NegotiationContext
from .models import (
    CrisisEvent,
    DEPARTMENT_ROLES,
    Message,
    MessageType,
    Plan,
    PlanStatus,
    Role,
    ScenarioState,
    ValidationResult,
    Vote,
    VoteDecision,
    Commitment,
    CommitmentStatus,
)
from .packages import (
    CRISIS_OVERRIDE_MAX_SACRIFICES,
    CRISIS_OVERRIDE_RISK_LIMIT,
    DEPARTMENTS,
    MODES,
    RESOURCE_ORDER,
    modes_for,
)


@dataclass
class NegotiationOutcome:
    status: str  # "APPROVED" | "INFEASIBLE" | "DEADLOCK"
    plan: Optional[Plan] = None
    rounds: int = 0
    process: Dict[str, object] = field(default_factory=dict)
    infeasibility: Optional[dict] = None
    detail: str = ""


class Commander:
    def __init__(self, council: AgentCouncil, state: ScenarioState, transcript: List[Message]):
        self.council = council
        self.state = state
        self.transcript = transcript
        self.plans: List[Plan] = []
        self.all_votes: List[Vote] = []
        self.commitments: List[Commitment] = []
        self.override_allowed = False
        self.saw_rejected_or_revised = False
        self.saw_sacrifice_refusal = False
        self.current_plan: Optional[Plan] = None
        # --- vote-recovery bookkeeping ---------------------------------
        self.rejection_log: List[dict] = []
        self.reconsider_count: Dict[str, int] = {}
        self.pending_directive: str = ""
        self.meaningful_revisions = 0
        self.reconsiderations = 0
        self._tried_selections = set()
        # --- Human-in-the-Loop (opt-in) ---------------------------------
        # A genuine approval gate for high-risk plans. It can never bypass the
        # deterministic validator or the four required ACCEPT votes.
        self.require_human_review = False
        self.pending_human_plan: Optional[Plan] = None
        self.human_decision: Optional[str] = None

    MAX_RECONSIDERATIONS = 1

    # -- logging helpers --------------------------------------------------
    def emit(self, mtype: MessageType, content: str, payload: Optional[dict] = None,
             recipient: Optional[Role] = None, engine: str = "system") -> Message:
        msg = Message(
            scenario_id=self.state.scenario_id,
            round=self.state.round,
            plan_version=self.state.plan_version,
            sender=Role.COMMANDER,
            recipient=recipient,
            type=mtype,
            content=content,
            payload=payload or {},
            engine=engine,
        )
        self.transcript.append(msg)
        return msg

    def _log_validation(self, plan: Plan, require_votes: bool = False):
        result = plan.validation
        assert result is not None
        verdict = "PASS" if result.passed else "FAIL"
        lines = " | ".join(
            f"{c.name}: {'PASS' if c.passed else 'FAIL'}" for c in result.checks
        )
        detail = " ".join(result.failed_reasons())
        self.emit(
            MessageType.INFO if result.passed else MessageType.OBJECTION,
            f"Deterministic validator on plan v{plan.version}: {verdict}. {lines}. {detail}",
            {"plan_version": plan.version, "passed": result.passed, "checks": [c.model_dump() for c in result.checks]},
        )

    def _note_rejection(self, plan: Plan):
        if plan.status in (PlanStatus.REJECTED, PlanStatus.INVALID):
            self.saw_rejected_or_revised = True

    # -- context factory --------------------------------------------------
    def _ctx(self, phase: str, **kwargs) -> NegotiationContext:
        kwargs.setdefault("plan_version", self.state.plan_version)
        return NegotiationContext(
            scenario_id=self.state.scenario_id,
            resources=self.state.resources,
            risk_limit=self.state.risk_limit,
            max_sacrifices=self.state.max_sacrifices,
            round=self.state.round,
            phase=phase,
            recent_messages=self.transcript,
            **kwargs,
        )

    def _structural_validate(self, selections: Dict[str, str], commitments: List[Commitment],
                             version: int) -> ValidationResult:
        return validator.validate_plan(
            selections=selections,
            resources=self.state.resources,
            risk_limit=self.state.risk_limit,
            max_sacrifices=self.state.max_sacrifices,
            plan_version=version,
            commitments=commitments,
            require_votes=False,
        )

    def _draft_plan(self, selections: Dict[str, str], commitments: List[Commitment],
                    note: str = "") -> Plan:
        self.state.plan_version += 1
        version = self.state.plan_version
        result = self._structural_validate(selections, commitments, version)
        plan = Plan(
            version=version,
            scenario_id=self.state.scenario_id,
            selections=dict(selections),
            commitments=list(commitments),
            status=PlanStatus.FEASIBLE if result.passed else PlanStatus.REJECTED,
            created_round=self.state.round,
            totals=result.totals,
            risk=result.risk,
            sacrifices=result.sacrifices,
            validation=result,
            note=note,
        )
        self.plans.append(plan)
        self.current_plan = plan
        self._note_rejection(plan)
        totals_line = " / ".join(f"{k[0].upper()}{plan.totals.get(k, 0)}" for k in RESOURCE_ORDER)
        sacs = ", ".join(plan.sacrifices) if plan.sacrifices else "none"
        self.emit(
            MessageType.STATE,
            f"Draft plan v{version}: "
            + ", ".join(f"{d}={plan.selections[d]}" for d in DEPARTMENTS)
            + f" | {totals_line} | risk {plan.risk} | sacrifice: {sacs}. ({note})",
            {"plan_version": version, "selections": plan.selections},
        )
        self._log_validation(plan)
        return plan

    # -- protocol ---------------------------------------------------------
    def _publish_crisis(self):
        res = " / ".join(f"{k[0].upper()}:{self.state.resources.get(k)}" for k in RESOURCE_ORDER)
        self.emit(
            MessageType.STATE,
            (
                f"CRISIS {self.state.scenario_id}: Council convened. Resources {res}. "
                f"Risk limit {self.state.risk_limit}, max sacrifice modes {self.state.max_sacrifices}. "
                f"Plan version v{self.state.plan_version}. Each department must select one "
                f"complete mode package; individual resource values are fixed."
            ),
            {"resources": self.state.resources, "risk_limit": self.state.risk_limit,
             "max_sacrifices": self.state.max_sacrifices},
        )

    def _collect_returns(self, beneficiary: str, version: int, accepted_commitments: List[Commitment]):
        """Offer and accept the two canonical return commitments for a sacrifice."""
        from .agents import SACRIFICE_RETURNS

        role_by_name = {r.value: r for r in Role}
        ben_role = role_by_name[beneficiary]
        already = {
            c.owner.value for c in accepted_commitments
            if c.beneficiary.value == beneficiary and c.is_accepted()
        }
        for owner_role, canonical_term in SACRIFICE_RETURNS.get(ben_role, []):
            if owner_role.value in already:
                continue
            if owner_role == Role.COMMANDER:
                commitment = Commitment(
                    owner=Role.COMMANDER,
                    beneficiary=ben_role,
                    return_term=canonical_term,
                    status=CommitmentStatus.OFFERED,
                    offered_round=self.state.round,
                    plan_version=version,
                )
                self.emit(
                    MessageType.COMMITMENT,
                    f"COMMITMENT: Commander promises {beneficiary}: {canonical_term}.",
                    {"commitment": commitment.model_dump(mode="json")},
                )
            else:
                owner_agent = self.council.get(owner_role)
                ctx = self._ctx(
                    "commitment", candidate=beneficiary, plan_version=version,
                    commitments=accepted_commitments.copy(),
                )
                commitment = owner_agent.make_commitment(ctx, ben_role)
                if commitment is None:
                    continue
                commitment.plan_version = version

            # Beneficiary decides whether to accept the return.
            ben_agent = self.council.get(ben_role)
            ctx = self._ctx(
                "commitment_response", candidate=beneficiary, plan_version=version,
                commitments=accepted_commitments + [commitment],
            )
            commitment = ben_agent.respond_to_commitment(ctx, commitment)
            self.commitments.append(commitment)
            if commitment.is_accepted():
                accepted_commitments.append(commitment)

        # The sacrificing agent states its final position.
        ben_agent = self.council.get(ben_role)
        ctx = self._ctx(
            "sacrifice_decision", candidate=beneficiary, plan_version=version,
            plan_selections=self.current_plan.selections if self.current_plan else {},
            commitments=accepted_commitments,
        )
        ben_agent.consider_sacrifice(ctx)
        self.saw_sacrifice_refusal = self.saw_sacrifice_refusal or self._detect_refusal()

    def _detect_refusal(self) -> bool:
        return any(
            ("refuse" in m.content.lower() or "refus" in m.content.lower())
            for m in self.transcript
            if m.type in (MessageType.OBJECTION, MessageType.COUNTEROFFER)
        )

    def run(self, min_rounds: int = 3, max_rounds: Optional[int] = None) -> NegotiationOutcome:
        self.state.status = "NEGOTIATING"
        max_rounds = max_rounds or self.state.max_rounds
        self._publish_crisis()

        # ---- Feasibility & override policy (Commander decision) ----------
        feasible = solver.best_plan(
            self.state.resources, self.state.risk_limit, self.state.max_sacrifices
        )
        if feasible is None and self.override_allowed:
            candidate = solver.best_plan(
                self.state.resources,
                CRISIS_OVERRIDE_RISK_LIMIT,
                CRISIS_OVERRIDE_MAX_SACRIFICES,
            )
            if candidate is not None:
                self._activate_override()
                feasible = candidate
        if feasible is None:
            report = solver.infeasibility_report(
                self.state.resources, self.state.risk_limit, self.state.max_sacrifices
            )
            self.emit(
                MessageType.INFEASIBLE,
                f"No feasible plan exists. {report.reason} Required change: {report.required_policy_change}",
                report.to_dict(),
            )
            self.state.status = "INFEASIBLE"
            return NegotiationOutcome(
                status="INFEASIBLE", rounds=self.state.round,
                infeasibility=report.to_dict(),
                process=self._process_report(),
                detail=report.required_policy_change,
            )

        # ---- Round 1: mode proposals -------------------------------------
        self.state.round = 1
        self.emit(MessageType.STATE, "Round 1: every department requests its preferred operating mode.")
        proposals: Dict[str, str] = {}
        for agent in self.council.all():
            ctx = self._ctx("propose")
            msg = agent.propose(ctx)
            proposals[agent.role.value] = msg.payload.get("mode_id", agent.profile.standard_mode)
        plan1 = self._draft_plan(proposals, [], note="round 1 proposals")

        # ---- Round 2: conflict / concessions -----------------------------
        self.state.round = 2
        self.emit(
            MessageType.STATE,
            (
                "Round 2: the drafted plan is invalid. Departments must object with "
                "numbers and declare their maximum concession and sacrifice stance."
            ),
        )
        concessions: Dict[str, str] = {}
        for agent in self.council.all():
            ctx = self._ctx("conflict", plan_selections=plan1.selections, validation=plan1.validation)
            msg = agent.respond_to_conflict(ctx)
            concessions[agent.role.value] = msg.payload.get("mode_id", agent.profile.restricted_mode)
        plan2 = self._draft_plan(concessions, [], note="round 2 concessions")
        self.saw_sacrifice_refusal = self.saw_sacrifice_refusal or self._detect_refusal()

        # ---- Round 3: feasible candidate + return commitments ------------
        self.state.round = 3
        self.emit(
            MessageType.STATE,
            (
                "Round 3: the Commander adopts the deterministically feasible candidate "
                f"and opens return negotiations. Candidate: "
                + solver.describe_candidate(feasible)
            ),
            {"candidate": feasible.as_dict()},
        )
        plan3, accepted = self._adopt_candidate(
            feasible.selections, note="round 3 feasible candidate", carry=[]
        )
        self._tried_selections.add(self._selection_key(plan3.selections))

        # ---- Round 4+: votes (with controlled recovery) ------------------
        vote_round = max(3, min_rounds) + 1
        while vote_round <= max_rounds:
            self.state.round = vote_round
            phase = "reconsider" if self.pending_directive else "vote"
            directive = self.pending_directive
            self.pending_directive = ""
            self.emit(
                MessageType.STATE,
                f"Round {vote_round}: voting on plan v{plan3.version}."
                + (" Structured reconsideration requested; no plan change is made."
                   if phase == "reconsider"
                   else " Any plan change clears earlier votes."),
                {"plan_version": plan3.version, "phase": phase},
            )

            # Re-vote the current version from scratch. A meaningful plan change
            # already created a new version, so old votes are simply not counted.
            self._clear_votes_for_version(plan3.version)
            fresh_votes: List[Vote] = []
            for agent in self.council.all():
                ctx = self._ctx(
                    phase,
                    plan_selections=plan3.selections,
                    validation=plan3.validation,
                    commitments=accepted,
                    directive=directive,
                )
                vote = agent.vote(ctx)
                fresh_votes.append(vote)
                self.all_votes.append(vote)
                self.emit(
                    MessageType.VOTE,
                    f"{agent.role.value} votes {vote.decision.value} on plan v{plan3.version}: {vote.reason}",
                    vote.model_dump(mode="json"),
                )

            full = validator.validate_plan(
                selections=plan3.selections,
                resources=self.state.resources,
                risk_limit=self.state.risk_limit,
                max_sacrifices=self.state.max_sacrifices,
                plan_version=plan3.version,
                commitments=accepted,
                votes=self.all_votes,
                require_votes=True,
            )
            if full.passed:
                plan3.validation = full
                # Human-in-the-Loop: a high-risk plan that is otherwise valid is
                # held for human sign-off. The human gate cannot be reached
                # unless the validator passed and all four ACCEPTs are present.
                if self.require_human_review and plan3.risk > 20:
                    plan3.status = PlanStatus.FEASIBLE
                    self.pending_human_plan = plan3
                    self.human_decision = None
                    self.state.status = "PENDING_HUMAN_REVIEW"
                    self.emit(
                        MessageType.STATE,
                        (
                            f"HUMAN REVIEW REQUIRED: plan v{plan3.version} passed the deterministic "
                            f"validator and holds four ACCEPT votes, but its risk score {plan3.risk} "
                            "exceeds 20. A human must approve it before it becomes final."
                        ),
                        {"plan_version": plan3.version, "risk": plan3.risk, "human_review": True},
                    )
                    return NegotiationOutcome(
                        status="PENDING_HUMAN_REVIEW",
                        plan=plan3,
                        rounds=self.state.round,
                        process=self._process_report(),
                        detail="Awaiting human approval (risk > 20).",
                    )
                plan3.status = PlanStatus.APPROVED
                self._approve(plan3)
                return NegotiationOutcome(
                    status="APPROVED",
                    plan=plan3,
                    rounds=self.state.round,
                    process=self._process_report(),
                    detail="Council reached unanimous agreement on a validated plan.",
                )

            self.saw_rejected_or_revised = True
            reject_votes = [v for v in fresh_votes if v.decision == VoteDecision.REJECT]
            self._record_rejections(reject_votes)
            self.emit(
                MessageType.OBJECTION,
                f"Consensus not reached on v{plan3.version}: " + " ".join(full.failed_reasons()),
                {
                    "plan_version": plan3.version,
                    "failed": full.failed_reasons(),
                    "rejections": [{"agent": v.agent.value, "reason": v.reason} for v in reject_votes],
                },
            )

            # (1) A concrete, fixable objection -> genuinely new plan version.
            revised = self._attempt_meaningful_revision(plan3, accepted, reject_votes)
            if revised is not None:
                plan3, accepted = revised
                self.reconsider_count.clear()
                vote_round += 1
                continue

            # (2) Unsupported / repeated rejection -> structured reconsideration
            #     on the SAME version (no version bump without a real change).
            pending = [
                v for v in reject_votes
                if self.reconsider_count.get(v.agent.value, 0) < self.MAX_RECONSIDERATIONS
            ]
            if pending:
                self._enter_reconsideration(pending)
                vote_round += 1
                continue

            # (3) Objections are legitimate and unrepairable, or already
            #     reconsidered. Never force acceptance: end honestly.
            self.state.status = "DEADLOCK"
            self.emit(
                MessageType.INFEASIBLE,
                "No feasible revision satisfies the outstanding objections on plan "
                f"v{plan3.version}: " + " | ".join(
                    f"{v.agent.value}: {v.reason}" for v in reject_votes
                ) + ". Result: DEADLOCK.",
                {"rejections": [{"agent": v.agent.value, "reason": v.reason} for v in reject_votes]},
            )
            return NegotiationOutcome(
                status="DEADLOCK", plan=plan3, rounds=self.state.round,
                process=self._process_report(),
                detail="Outstanding objections could not be resolved without overriding safety.",
            )

        self.state.status = "DEADLOCK"
        self.emit(
            MessageType.INFEASIBLE,
            f"Maximum rounds ({max_rounds}) reached without unanimous agreement. Result: DEADLOCK.",
        )
        return NegotiationOutcome(
            status="DEADLOCK", plan=self.current_plan, rounds=self.state.round,
            process=self._process_report(),
            detail="Round limit reached without consensus.",
        )

    # -- recovery helpers -------------------------------------------------
    @staticmethod
    def _selection_key(selections: Dict[str, str]):
        return tuple(sorted(selections.items()))

    def _clear_votes_for_version(self, version: int):
        self.all_votes = [v for v in self.all_votes if v.plan_version != version]

    def _record_rejections(self, reject_votes: List[Vote]):
        for v in reject_votes:
            self.rejection_log.append({
                "agent": v.agent.value,
                "plan_version": v.plan_version,
                "round": v.round,
                "reason": v.reason,
                "engine": v.engine,
            })

    def _enter_reconsideration(self, pending: List[Vote]):
        self.reconsiderations += 1
        names = ", ".join(v.agent.value for v in pending)
        for v in pending:
            self.reconsider_count[v.agent.value] = self.reconsider_count.get(v.agent.value, 0) + 1
        self.pending_directive = (
            "The Council asks each rejecting department to identify a concrete, actionable "
            "safety or fairness issue with the current valid plan. If none exists, apply your "
            "deterministic policy: ACCEPT a validator-safe plan that meets your terms, and never "
            "accept an unsafe or uncompensated plan."
        )
        self.emit(
            MessageType.STATE,
            f"Structured reconsideration requested from: {names}. No plan change is made, so "
            "this stays on the current plan version.",
            {"reconsidering": [v.agent.value for v in pending], "directive": self.pending_directive},
        )

    def _adopt_candidate(self, selections: Dict[str, str], note: str,
                         carry: Optional[List[Commitment]] = None):
        """Draft a plan and (re)negotiate the return commitments it needs.

        Accepted commitments whose beneficiary still sacrifices are preserved;
        missing returns are then collected. Returns ``(plan, accepted)``.
        """
        sacrifices = [
            d for d in DEPARTMENTS
            if selections.get(d) in MODES and MODES[selections[d]].is_sacrifice
        ]
        carried = [
            c for c in (carry or [])
            if c.is_accepted() and c.beneficiary.value in sacrifices
        ]
        accepted: List[Commitment] = list(carried)
        plan = self._draft_plan(selections, accepted, note=note)
        for beneficiary in plan.sacrifices:
            self._collect_returns(beneficiary, plan.version, accepted)
        plan.commitments = list(accepted)
        plan.validation = self._structural_validate(plan.selections, accepted, plan.version)
        plan.status = PlanStatus.FEASIBLE if plan.validation.passed else PlanStatus.REJECTED
        self._note_rejection(plan)
        self._log_validation(plan)
        return plan, accepted

    def _revision_targets(self, plan: Plan, reject_votes: List[Vote]):
        """Derive concrete, fixable requirements from the rejection reasons."""
        avoid_sacrifice = set()
        wanted_modes: Dict[str, str] = {}
        for v in reject_votes:
            agent = self.council.get(v.agent)
            dept = v.agent.value
            reason = (v.reason or "").lower()
            if dept in plan.sacrifices and (
                agent.profile.sacrifice_mode.lower() in reason
                or "sacrific" in reason
                or "refus" in reason
            ):
                avoid_sacrifice.add(dept)
            for mid in modes_for(dept):
                if mid.lower() in reason and mid != plan.selections.get(dept):
                    wanted_modes[dept] = mid
        return avoid_sacrifice, wanted_modes

    def _attempt_meaningful_revision(self, plan: Plan, accepted: List[Commitment],
                                     reject_votes: List[Vote]):
        """Turn a concrete objection into a genuinely different feasible plan.

        Returns ``(plan, accepted)`` on success, or ``None`` when no meaningful
        revision can be made (so the caller requests reconsideration instead).
        """
        avoid_sacrifice, wanted_modes = self._revision_targets(plan, reject_votes)

        need_returns = bool(
            plan.validation and any(
                (not c.passed) and c.name == "Return agreement" for c in plan.validation.checks
            )
        )

        # Without a concrete, fixable target we must NOT invent a plan change:
        # that would bump the version for no reason. Request reconsideration.
        if not avoid_sacrifice and not wanted_modes and not need_returns:
            return None

        candidates = solver.enumerate_feasible(
            self.state.resources, self.state.risk_limit, self.state.max_sacrifices
        )
        for cand in candidates:
            key = self._selection_key(cand.selections)
            if cand.selections == plan.selections or key in self._tried_selections:
                continue
            if any(d in cand.sacrifices for d in avoid_sacrifice):
                continue
            if wanted_modes and not any(
                cand.selections.get(d) == m for d, m in wanted_modes.items()
            ):
                continue
            self._tried_selections.add(key)
            new_plan, new_accepted = self._adopt_candidate(
                cand.selections, note="revised after a concrete objection", carry=accepted
            )
            if not new_plan.validation or not new_plan.validation.passed:
                continue
            self.meaningful_revisions += 1
            self.emit(
                MessageType.STATE,
                f"Meaningful plan revision adopted: plan v{new_plan.version} changes the package "
                "selection in response to the objection. Earlier votes are cleared.",
                {"plan_version": new_plan.version, "selections": dict(new_plan.selections),
                 "sacrifices": list(new_plan.sacrifices)},
            )
            return new_plan, new_accepted

        # A fixable commitment gap with no selection change is still a genuine
        # revision (the commitments change), so it gets a new version too.
        if need_returns:
            carry = [c for c in accepted if c.is_accepted()]
            new_plan, new_accepted = self._adopt_candidate(
                plan.selections, note="revised return commitments", carry=carry
            )
            if len(new_accepted) > len(carry):
                self.meaningful_revisions += 1
                self.emit(
                    MessageType.STATE,
                    f"Meaningful revision: plan v{new_plan.version} updates the return commitments. "
                    "Earlier votes are cleared.",
                    {"plan_version": new_plan.version},
                )
                return new_plan, new_accepted
        return None


    def _activate_override(self):
        self.state.override_active = True
        self.state.risk_limit = CRISIS_OVERRIDE_RISK_LIMIT
        self.state.max_sacrifices = CRISIS_OVERRIDE_MAX_SACRIFICES
        self.emit(
            MessageType.STATE,
            (
                f"CRISIS OVERRIDE activated: risk limit raised to {CRISIS_OVERRIDE_RISK_LIMIT} "
                f"and up to {CRISIS_OVERRIDE_MAX_SACRIFICES} Sacrifice modes permitted. "
                "Every sacrificing department still requires two accepted returns."
            ),
        )

    def _approve(self, plan: Plan):
        self.state.status = "APPROVED"
        self.emit(
            MessageType.APPROVAL,
            (
                f"APPROVED plan v{plan.version}. Validator PASS and four ACCEPT votes on "
                f"the same version. Sacrifice: {', '.join(plan.sacrifices) or 'none'}, "
                f"risk {plan.risk}."
            ),
            {"plan_version": plan.version, "validation": plan.validation.model_dump(mode="json") if plan.validation else None},
        )

    # -- Human-in-the-Loop approval --------------------------------------
    def approve_as_human(self):
        """Human sign-off on a pending high-risk plan.

        Returns ``(ok, message)``. The deterministic validator is re-run with
        the full vote set first: a human can never approve an unsafe plan or one
        that lacks four ACCEPT votes on the same version.
        """
        plan = self.pending_human_plan
        if plan is None:
            return False, "No plan is awaiting human review."

        full = validator.validate_plan(
            selections=plan.selections,
            resources=self.state.resources,
            risk_limit=self.state.risk_limit,
            max_sacrifices=self.state.max_sacrifices,
            plan_version=plan.version,
            commitments=plan.commitments,
            votes=self.all_votes,
            require_votes=True,
        )
        if not full.passed:
            self.emit(
                MessageType.OBJECTION,
                "HUMAN APPROVAL BLOCKED by the deterministic validator: "
                + "; ".join(full.failed_reasons()),
                {"plan_version": plan.version, "human_review": True, "passed": False},
            )
            return False, "Blocked by validator: " + "; ".join(full.failed_reasons())

        plan.validation = full
        plan.status = PlanStatus.APPROVED
        self.human_decision = "APPROVED"
        self.pending_human_plan = None
        self.emit(
            MessageType.APPROVAL,
            (
                f"HUMAN APPROVAL granted for plan v{plan.version} (risk {plan.risk}). "
                "Deterministic validator re-confirmed PASS and four ACCEPT votes were present."
            ),
            {"plan_version": plan.version, "human_review": True, "approved_by": "human"},
        )
        self._approve(plan)
        return True, "approved"

    def reject_as_human(self, reason: str = ""):
        """Human reviewer rejects a pending high-risk plan."""
        plan = self.pending_human_plan
        if plan is None:
            return False, "No plan is awaiting human review."
        plan.status = PlanStatus.REJECTED
        self.human_decision = "REJECTED"
        self.pending_human_plan = None
        self.state.status = "REJECTED_BY_HUMAN"
        self.emit(
            MessageType.OBJECTION,
            f"HUMAN REVIEW rejected plan v{plan.version}. {reason}".strip(),
            {"plan_version": plan.version, "human_review": True},
        )
        return True, "rejected"

    def _process_report(self) -> Dict[str, object]:
        accepted_by_beneficiary: Dict[str, int] = {}
        for c in self.commitments:
            if c.is_accepted():
                accepted_by_beneficiary[c.beneficiary.value] = accepted_by_beneficiary.get(c.beneficiary.value, 0) + 1
        return {
            "rounds_used": self.state.round,
            "at_least_three_rounds": self.state.round >= 3,
            "rejected_or_revised_proposal": self.saw_rejected_or_revised,
            "sacrifice_refused_by_an_agent": self.saw_sacrifice_refusal,
            "accepted_returns_per_sacrifice": accepted_by_beneficiary,
            "override_active": self.state.override_active,
            "plan_versions": [p.version for p in self.plans],
            "meaningful_revisions": self.meaningful_revisions,
            "reconsiderations": self.reconsiderations,
            "rejections_logged": len(self.rejection_log),
            "human_review_required": self.require_human_review,
            "human_decision": self.human_decision,
            "pending_human_version": self.pending_human_plan.version if self.pending_human_plan else None,
        }
