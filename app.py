"""ARES Accord - Mission Control dashboard (Streamlit).

Run with:
    streamlit run app.py

The dashboard is the mandatory graphical interface: it shows the live council
transcript, resource pool, mode board, deterministic validation, plan status,
voting, and lets a judge inject a new emergency without touching code.

This module is presentation only. It calls the existing backend (engine,
solver, validator, storage) unchanged; no safety rule lives here.
"""

from __future__ import annotations

import datetime
import html
import json
from typing import Dict, List

import pandas as pd
import streamlit as st

from ares.agents import ROLE_PROFILES
from ares.config import settings
from ares.engine import AresEngine
from ares.llm import LLMClient
from ares.models import MessageType, PlanStatus, Role, engine_badge
from ares.packages import (
    CRISIS_OVERRIDE_RISK_LIMIT,
    DEFAULT_RESOURCES,
    MODES,
    MODE_TABLE,
    RESOURCE_ORDER,
    modes_for,
)

st.set_page_config(
    page_title="ARES Accord - Mission Control",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

PRESET_EVENTS: Dict[str, dict] = {
    "Solar Aftershock (Power -4)": {
        "event_id": "CRISIS_001", "event_name": "Solar Aftershock",
        "description": "Secondary solar flare. Power generation drops.",
        "impact": {"power_reduction": 4},
    },
    "Water Contamination (Water -3)": {
        "event_id": "CRISIS_002", "event_name": "Water Contamination",
        "description": "Coolant leaks into the water store.",
        "impact": {"water_reduction": 3},
    },
    "Secondary Oxygen Leak (Oxygen -3)": {
        "event_id": "CRISIS_003", "event_name": "Secondary Oxygen Leak",
        "description": "A new hull breach bleeds oxygen.",
        "impact": {"oxygen_reduction": 3},
    },
    "Rover Actuator Failure (Robot -4)": {
        "event_id": "CRISIS_004", "event_name": "Rover Actuator Failure",
        "description": "The repair rover loses actuator time.",
        "impact": {"robot_reduction": 4},
    },
    "Relay Interference (Bandwidth -2)": {
        "event_id": "CRISIS_005", "event_name": "Relay Interference",
        "description": "Comms relay interference reduces bandwidth.",
        "impact": {"bandwidth_reduction": 2},
    },
}

RESOURCE_ICON = {"power": "⚡", "water": "💧", "oxygen": "🫁", "robot": "🤖", "bandwidth": "📡"}

STATUS_KIND = {
    "APPROVED": "ok",
    "NEGOTIATING": "warn",
    "RENEGOTIATING": "warn",
    "PENDING_HUMAN_REVIEW": "warn",
    "INFEASIBLE": "danger",
    "DEADLOCK": "danger",
    "REJECTED_BY_HUMAN": "danger",
}
STATUS_ICON = {
    "APPROVED": "🟢", "NEGOTIATING": "🟡", "RENEGOTIATING": "🟠",
    "INFEASIBLE": "🔴", "DEADLOCK": "🔴", "PENDING_HUMAN_REVIEW": "🟠",
    "REJECTED_BY_HUMAN": "🔴",
}

ARES_CSS = """
<style>
.ares-hero{border:1px solid #26333f;border-left:5px solid #ff5a1f;border-radius:14px;
  padding:16px 22px;background:linear-gradient(90deg,#131c25 0%,#0c1218 70%);margin-bottom:10px;}
.ares-hero-title{font-size:30px;font-weight:800;letter-spacing:4px;color:#f5f9ff;margin:0;line-height:1.1;}
.ares-hero-sub{color:#8ea0b0;font-size:11.5px;letter-spacing:2.2px;margin-top:6px;text-transform:uppercase;}
.ares-pill{display:inline-block;padding:2px 11px;border-radius:999px;font-size:12px;font-weight:700;
  letter-spacing:.4px;border:1px solid transparent;margin-right:5px;white-space:nowrap;}
.pill-ok{background:rgba(46,204,113,.14);color:#4ade80;border-color:rgba(46,204,113,.42);}
.pill-warn{background:rgba(255,176,32,.14);color:#ffc14d;border-color:rgba(255,176,32,.42);}
.pill-danger{background:rgba(239,68,68,.16);color:#ff7070;border-color:rgba(239,68,68,.46);}
.pill-neutral{background:rgba(147,164,179,.14);color:#c3d0db;border-color:rgba(147,164,179,.34);}
.pill-live{background:rgba(46,204,113,.16);color:#4ade80;border-color:rgba(46,204,113,.48);}
.pill-fallback{background:rgba(255,90,31,.16);color:#ff8a5c;border-color:rgba(255,90,31,.5);}
.pill-system{background:rgba(59,130,246,.14);color:#7db4ff;border-color:rgba(59,130,246,.4);}
.ares-tile{background:#121a22;border:1px solid #26333f;border-radius:12px;padding:12px 14px;height:100%;}
.ares-tile .lbl{color:#8ea0b0;font-size:11px;letter-spacing:1.4px;text-transform:uppercase;}
.ares-tile .val{color:#f0f5fa;font-size:22px;font-weight:750;margin-top:3px;}
.ares-card{background:#121a22;border:1px solid #26333f;border-radius:12px;padding:14px 15px;height:100%;}
.ares-card .role{color:#8ea0b0;font-size:10.5px;letter-spacing:1.6px;text-transform:uppercase;}
.ares-card h4{margin:2px 0 0 0;font-size:16px;color:#f0f5fa;}
.ares-card .goal{color:#c3d0db;font-size:12px;margin-top:8px;line-height:1.4;min-height:34px;}
.ares-kv{display:flex;justify-content:space-between;gap:8px;font-size:12px;color:#c3d0db;
  margin-top:6px;border-top:1px dashed #22303c;padding-top:6px;}
.ares-kv .k{color:#8ea0b0;}
.ares-res{margin:12px 0;}
.ares-res .top{display:flex;justify-content:space-between;font-size:13px;color:#dbe6ef;}
.ares-res .bar{height:10px;background:#0b1119;border:1px solid #22303c;border-radius:6px;overflow:hidden;margin-top:5px;}
.ares-res .fill{height:100%;background:linear-gradient(90deg,#ff5a1f,#ffb020);}
.ares-res .fill.over{background:linear-gradient(90deg,#ef4444,#ff5a1f);}
.ares-chip{display:inline-block;padding:4px 10px;border-radius:8px;margin:2px 3px 2px 0;font-size:12px;
  border:1px solid #26333f;background:#0f151c;color:#c3d0db;}
.chip-approved{border-color:rgba(46,204,113,.5);color:#4ade80;}
.chip-stale{border-color:rgba(255,176,32,.5);color:#ffc14d;}
.chip-invalid,.chip-rejected{border-color:rgba(239,68,68,.5);color:#ff7070;}
.chip-feasible{border-color:rgba(59,130,246,.5);color:#7db4ff;}
.ares-arrow{color:#5b6770;margin:0 3px;}
.ares-legend{color:#8ea0b0;font-size:12px;margin:2px 0 10px 0;}
.ares-section{color:#f0f5fa;font-size:15px;font-weight:700;letter-spacing:.6px;margin:6px 0 8px 0;
  border-left:3px solid #ff5a1f;padding-left:9px;}
.msg-text{background:#0f151c;border:1px solid #26333f;border-left:3px solid #ff5a1f;
  border-radius:8px;padding:10px 12px;color:#dbe6ef;font-size:13.5px;line-height:1.55;
  white-space:pre-wrap;word-break:break-word;}
</style>
"""


# ---------------------------------------------------------------------------
# Small presentation helpers (no backend logic)
# ---------------------------------------------------------------------------
def pill(text: str, kind: str = "neutral") -> str:
    return f'<span class="ares-pill pill-{kind}">{text}</span>'


def section(title: str) -> None:
    st.markdown(f'<div class="ares-section">{title}</div>', unsafe_allow_html=True)


def message_block(content: str) -> str:
    """Render a transcript message in a readable, HTML-escaped block."""
    return f'<div class="msg-text">{html.escape(content)}</div>'


def tile(label: str, value: str) -> str:
    return f'<div class="ares-tile"><div class="lbl">{label}</div><div class="val">{value}</div></div>'


def status_kind(status: str) -> str:
    return STATUS_KIND.get(status, "neutral")


def engine_pill_kind(engine: str) -> str:
    return {"llm": "live", "fallback": "fallback", "system": "system"}.get(engine, "system")


def resource_bars_html(snap: dict) -> str:
    parts = []
    for key in RESOURCE_ORDER:
        avail = snap["available"].get(key, 0)
        used = snap["used"].get(key, 0)
        reserve = snap["reserve"].get(key, 0)
        pct = int(min(100, (used / avail * 100) if avail > 0 else 0))
        over = reserve < 0
        reserve_color = "#ff7070" if over else "#4ade80"
        parts.append(
            f'<div class="ares-res">'
            f'<div class="top"><span>{RESOURCE_ICON[key]} {key.title()}</span>'
            f'<span>{used} used · {avail} available · '
            f'<b style="color:{reserve_color}">{reserve} reserve</b></span></div>'
            f'<div class="bar"><div class="fill {"over" if over else ""}" '
            f'style="width:{pct}%"></div></div></div>'
        )
    return "".join(parts)


def timeline_html(plans) -> str:
    chips = []
    for i, p in enumerate(plans):
        kind = {
            "APPROVED": "chip-approved", "STALE": "chip-stale",
            "INVALID": "chip-invalid", "REJECTED": "chip-rejected",
            "INFEASIBLE": "chip-invalid", "FEASIBLE": "chip-feasible",
        }.get(p.status.value, "")
        chips.append(
            f'<span class="ares-chip {kind}">v{p.version} · {p.status.value} · R{p.created_round}</span>'
        )
        if i < len(plans) - 1:
            chips.append('<span class="ares-arrow">→</span>')
    return "".join(chips) or '<span class="ares-legend">No plans drafted yet.</span>'


def agent_card_html(role_label: str, name: str, goal: str, mode: str, risk: str,
                    vote_html: str, msgs: int, last_round: str) -> str:
    return (
        f'<div class="ares-card">'
        f'<div class="role">{role_label}</div><h4>{name}</h4>'
        f'<div class="goal">{goal}</div>'
        f'<div class="ares-kv"><span class="k">Mode</span><span>{mode}</span></div>'
        f'<div class="ares-kv"><span class="k">Risk</span><span>{risk}</span></div>'
        f'<div class="ares-kv"><span class="k">Vote</span><span>{vote_html}</span></div>'
        f'<div class="ares-kv"><span class="k">Messages</span><span>{msgs} · last R{last_round}</span></div>'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Engine/session helpers
# ---------------------------------------------------------------------------
def get_engine() -> AresEngine:
    if "engine" not in st.session_state:
        # Default to the reliable, key-free deterministic mode for judging.
        st.session_state.engine = AresEngine(llm=LLMClient(provider="fallback"), enabled=True)
    engine = st.session_state.engine
    engine.set_human_review(bool(st.session_state.get("human_review_toggle", False)))
    return engine


def rebuild_engine(offline: bool, human_review: bool = False) -> AresEngine:
    """Create the engine for the chosen mode.

    ``offline=True`` pins the clearly-labelled deterministic agents, so judging
    never depends on a key or the network. ``offline=False`` requests the live
    model; if no key is configured the client resolves to fallback and the run
    still completes, with every message honestly tagged.
    """
    llm = LLMClient(provider="fallback") if offline else LLMClient()
    st.session_state.engine = AresEngine(llm=llm, enabled=True)
    st.session_state.engine.set_human_review(human_review)
    return st.session_state.engine


@st.cache_data(show_spinner=False)
def transcript_rows(messages):
    df = pd.DataFrame([m.to_row() for m in messages])
    if not df.empty:
        df.insert(7, "source", df["engine"].map(engine_badge))
    return df


def _msgs_by_sender(engine: AresEngine):
    counts: Dict[Role, int] = {}
    last_round: Dict[Role, int] = {}
    for m in engine.transcript:
        counts[m.sender] = counts.get(m.sender, 0) + 1
        last_round[m.sender] = max(last_round.get(m.sender, 0), m.round)
    return counts, last_round


def _latest_vote(engine: AresEngine, role: Role, version: int):
    for v in reversed(engine.commander.all_votes):
        if v.agent == role and v.plan_version == version:
            return v
    return None


st.markdown(ARES_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar: judge controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🛰️ Judge Console")
    st.caption("Everything a judge needs: mode, resource pool, emergency injection, exports.")

    with st.container(border=True):
        st.markdown("#### 1 · Agent mode")
        live_available = settings.resolved_provider() in ("gemini", "openai")
        live_option = (
            f"🌐 LIVE — {settings.describe()}"
            if live_available
            else "🌐 LIVE (no API key — will run offline fallback)"
        )
        mode_choice = st.radio(
            "Agent mode",
            ["🔒 OFFLINE (deterministic, recommended)", live_option],
            index=0,
            help=(
                "OFFLINE pins the clearly-labelled deterministic agents and needs no key or network. "
                "LIVE asks the configured model to compose messages; if a call fails or is rate-limited "
                "the agent visibly FALLS BACK and the message is tagged FALLBACK, never as live AI."
            ),
        )
        offline_selected = mode_choice.startswith("🔒")
        human_review = st.toggle(
            "Human approval for high-risk plans (risk > 20)",
            value=False,
            key="human_review_toggle",
            help=(
                "When on, a plan that passed the validator and holds all four ACCEPT votes is held "
                "for human sign-off if its risk exceeds 20. The human can never bypass the validator "
                "or the required votes."
            ),
        )
        if st.button("🔁 New run with this setting", width="stretch"):
            rebuild_engine(offline_selected, human_review)
            st.success("Engine recreated. Start the crisis below.")

    with st.container(border=True):
        st.markdown("#### 2 · Resource pool")
        st.caption("Defaults match the challenge guide. Change them, then start.")
        resource_inputs = {}
        for key in RESOURCE_ORDER:
            resource_inputs[key] = st.number_input(
                f"{RESOURCE_ICON[key]} {key.title()}", min_value=0, max_value=200,
                value=int(DEFAULT_RESOURCES[key]), step=1, key=f"res_{key}",
            )
        risk_limit = st.number_input("Risk limit", min_value=4, max_value=40, value=24, step=1)
        if st.button("🚀 Start / restart crisis", type="primary", width="stretch"):
            engine = rebuild_engine(offline_selected, human_review)
            with st.spinner("Council is negotiating..."):
                engine.start(resources=resource_inputs, risk_limit=int(risk_limit))
            st.rerun()
        if st.button("🧹 Reset everything", width="stretch"):
            rebuild_engine(offline_selected, human_review)
            st.rerun()

    with st.container(border=True):
        st.markdown("#### 3 · Inject emergency")
        st.caption("Mid-run crisis. Pick a practice event or paste any unseen JSON.")

        def _sync_preset_json():
            st.session_state["event_json"] = json.dumps(
                PRESET_EVENTS[st.session_state["preset_name"]], indent=2
            )

        preset_name = st.selectbox(
            "Practice event", list(PRESET_EVENTS.keys()),
            key="preset_name", on_change=_sync_preset_json,
        )
        if "event_json" not in st.session_state:
            st.session_state["event_json"] = json.dumps(PRESET_EVENTS[preset_name], indent=2)
        custom_json = st.text_area(
            "Or paste judge event JSON (any unseen scenario)",
            height=170,
            key="event_json",
        )
        if st.button("⚡ Inject event & renegotiate", width="stretch"):
            engine = get_engine()
            try:
                raw = json.loads(custom_json)
            except json.JSONDecodeError as exc:
                st.error(f"Invalid JSON: {exc}")
            else:
                with st.spinner("Renegotiating after the emergency..."):
                    engine.inject_event(raw)
                st.rerun()

    with st.container(border=True):
        st.markdown("#### 4 · Export")
        engine = get_engine()
        if engine.transcript:
            engine.export("exports")
            st.caption("Saved to `./exports/` after every run.")
            transcript_json = json.dumps(
                [m.model_dump(mode="json") for m in engine.transcript], indent=2
            )
            st.download_button(
                "⬇️ council_transcript.json", transcript_json,
                file_name="council_transcript.json", mime="application/json", width="stretch",
            )
            if engine.final_plan:
                from ares.storage import plan_to_output_schema

                final_json = json.dumps(
                    plan_to_output_schema(engine.final_plan, engine.state), indent=2
                )
                st.download_button(
                    "⬇️ final_allocation.json", final_json,
                    file_name="final_allocation.json", mime="application/json", width="stretch",
                )
        else:
            st.caption("Run the crisis to enable exports.")


# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------
engine = get_engine()

st.markdown(
    '<div class="ares-hero">'
    '<div class="ares-hero-title">ARES ACCORD</div>'
    '<div class="ares-hero-sub">Mars Colony · Multi-Agent Emergency Negotiation · Mission Control</div>'
    '</div>',
    unsafe_allow_html=True,
)

# --- Prominent, always-visible mode + provenance banner --------------------
# LIVE must mean a real model request actually succeeded, never merely that a
# key is configured. If the model is selected but no call has returned usable
# JSON, the banner honestly reports FALLBACK.
requested_live = engine.llm.available
live_verified = requested_live and engine.llm.live_verified
n_llm = sum(1 for m in engine.transcript if m.engine == "llm")
n_fb = sum(1 for m in engine.transcript if m.engine == "fallback")
n_sys = len(engine.transcript) - n_llm - n_fb
if not requested_live:
    mode_badge = "🔒 OFFLINE (deterministic)"
elif live_verified:
    mode_badge = "🟢 LIVE (verified by a real model reply)"
elif engine.transcript:
    mode_badge = "🔴 FALLBACK (LIVE requested, no model reply yet)"
else:
    mode_badge = "🌐 LIVE selected (not yet verified)"
backend = engine.llm.describe()
provenance = (
    f"**Agent mode:** {mode_badge} &nbsp;·&nbsp; **Backend:** {backend} &nbsp;·&nbsp; "
    f"**Provenance this run:** 🟢 {n_llm} LIVE AI · 🔴 {n_fb} FALLBACK · ⚙️ {n_sys} SYSTEM"
)
st.markdown(provenance)
if requested_live and not live_verified and engine.transcript:
    err = engine.llm.last_error_safe or "provider unavailable"
    st.warning(
        f"⚠️ LIVE mode is selected but no model request succeeded, so all {n_fb} agent "
        f"message(s) used the deterministic **FALLBACK** ({err}). Fallback output is clearly "
        "labelled and is never presented as live AI output."
    )
elif requested_live and live_verified and n_fb:
    err = engine.llm.last_error_safe or "a model call failed"
    st.warning(
        f"⚠️ LIVE verified ({n_llm} model message(s)), but {n_fb} message(s) fell back after a "
        f"failed call ({err}). Those messages are tagged **FALLBACK** and are not shown as live AI."
    )

if not engine.started:
    st.info(
        "**Not started.** Choose a mode and resource pool in the sidebar, then press "
        "**🚀 Start / restart crisis**. The Commander opens Round 1 and the five agents "
        "negotiate. Then inject an emergency to watch the colony recover."
    )
    with st.expander("Scenario rules at a glance", expanded=True):
        st.markdown(
            f"""
- **Resource pool:** Power {DEFAULT_RESOURCES['power']}, Water {DEFAULT_RESOURCES['water']}, \
Oxygen {DEFAULT_RESOURCES['oxygen']}, Robot {DEFAULT_RESOURCES['robot']}, Bandwidth {DEFAULT_RESOURCES['bandwidth']}
- **Modes:** exactly one complete package per department; values are fixed.
- **Risk:** combined risk ≤ 24 (Crisis Override may raise to {CRISIS_OVERRIDE_RISK_LIMIT}).
- **Sacrifice:** at most 1 department (2 under Crisis Override), each needs **two accepted returns**.
- **Approval:** validator PASS + four ACCEPT votes on the same plan version, after ≥ 3 rounds.

**Provenance legend:** 🟢 LIVE AI = real model output · 🔴 FALLBACK = deterministic policy
(model unavailable / timed-out / rate-limited / malformed) · ⚙️ SYSTEM = Commander/validator.
"""
        )
    st.stop()

snap = engine.snapshot()
status = snap["status"]
event = engine.state.events[-1] if engine.state.events else None
plan = engine.commander.current_plan
counts, last_round = _msgs_by_sender(engine)

# --- Crisis status tiles ---------------------------------------------------
t1, t2, t3, t4, t5 = st.columns(5)
with t1:
    st.markdown(tile("Scenario", snap["scenario_id"]), unsafe_allow_html=True)
with t2:
    st.markdown(
        tile("Crisis status", f'{STATUS_ICON.get(status, "⚪")} {status}'), unsafe_allow_html=True
    )
with t3:
    st.markdown(tile("Round", str(snap["round"])), unsafe_allow_html=True)
with t4:
    st.markdown(tile("Plan version", f'v{snap["plan_version"]}'), unsafe_allow_html=True)
with t5:
    st.markdown(
        tile("Risk limit", f'{snap["risk_limit"]} / max sac {snap["max_sacrifices"]}'),
        unsafe_allow_html=True,
    )

if event:
    st.warning(f"**{event.event_name}** — {event.description}  (deltas: {event.resource_deltas})")
if snap["override_active"]:
    st.error(
        f"⚠️ Crisis Override active: risk limit {snap['risk_limit']}, "
        f"max sacrifices {snap['max_sacrifices']}"
    )

# --- Human-in-the-loop gate -------------------------------------------------
pending_human = engine.commander.pending_human_plan
if pending_human is not None:
    st.warning(
        f"🧑‍⚖️ Human-in-the-Loop: plan v{pending_human.version} passed the deterministic validator "
        f"and holds four ACCEPT votes, but its risk score {pending_human.risk} exceeds 20. "
        "A human reviewer must approve it before it becomes final."
    )
    hcol1, hcol2 = st.columns(2)
    if hcol1.button("✅ Approve as human reviewer", type="primary", width="stretch"):
        ok, message = engine.approve_as_human()
        if ok:
            st.success("Human approval granted. The plan is now APPROVED.")
        else:
            st.error(message)
        st.rerun()
    if hcol2.button("❌ Reject as human reviewer", width="stretch"):
        engine.reject_as_human("reviewer rejected the high-risk plan")
        st.rerun()

tab_mission, tab_agents, tab_transcript, tab_modes, tab_validation, tab_plans = st.tabs(
    ["🛰️ Mission Control", "👥 Agents", "🗣️ Council Transcript",
     "🧩 Mode Board", "✅ Validation", "🗳️ Plans & Votes"]
)

# --- Mission Control -------------------------------------------------------
with tab_mission:
    left, right = st.columns([3, 2])

    with left:
        section("Resource pool — availability / usage / reserve")
        st.markdown(resource_bars_html(snap), unsafe_allow_html=True)

    with right:
        section("Selected modes")
        if plan:
            mrows = []
            for dept in MODE_TABLE:
                mid = plan.selections.get(dept, "-")
                m = MODES.get(mid)
                mrows.append({
                    "Department": dept,
                    "Mode": mid,
                    "Kind": m.kind if m else "-",
                    "Risk": m.risk if m else "-",
                })
            st.dataframe(pd.DataFrame(mrows), width="stretch", hide_index=True)
            st.markdown(
                f'Plan **v{plan.version}** {pill(plan.status.value, status_kind(plan.status.value))} '
                f'total risk **{plan.risk}** · sacrifice: '
                f'**{", ".join(plan.sacrifices) or "none"}**',
                unsafe_allow_html=True,
            )
        else:
            st.info("No plan drafted yet.")

    st.divider()
    section("Negotiation timeline")
    st.markdown(timeline_html(engine.commander.plans), unsafe_allow_html=True)

    if engine.crisis_log:
        last = engine.crisis_log[-1]
        st.markdown(
            f'**New negotiation started** after **{last["event"]["event_name"]}** '
            f'(rounds used: {last["rounds_used"]}) → '
            f'{pill(last["outcome_status"], status_kind(last["outcome_status"]))}',
            unsafe_allow_html=True,
        )
        if last.get("stale_note"):
            st.caption(f"Plan review: {last['stale_note']}")

    section("Model call log (this session)")
    calls = getattr(engine.llm, "call_log", [])
    if calls:
        rows = [{
            "time": datetime.datetime.fromtimestamp(c["ts"]).strftime("%H:%M:%S"),
            "provider": c.get("provider", ""),
            "model": c.get("model", ""),
            "result": "✅ OK" if c.get("ok") else "❌ FAIL",
            "kind": c.get("kind") or "-",
            "latency (s)": f"{float(c.get('elapsed') or 0):.2f}",
            "detail (redacted)": (c.get("error") or "")[:140],
        } for c in calls]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption(
            "Every provider call this session. Errors are redacted and truncated, so no "
            "API key or request contents are shown. Same data is written to "
            "`exports/llm_operations_log.json`."
        )
    else:
        st.caption("No model calls recorded (offline/deterministic path, or run not started).")

# --- Agents ----------------------------------------------------------------
with tab_agents:
    section("Council agents")
    st.markdown(
        '<div class="ares-legend">Each card is derived from real run state: the mode it currently '
        'holds, its latest vote on the current plan version, and how many messages it has sent.</div>',
        unsafe_allow_html=True,
    )

    cur_version = snap["plan_version"]
    cmd_msgs = counts.get(Role.COMMANDER, 0)
    cmd_round = last_round.get(Role.COMMANDER, 0)
    st.markdown(
        agent_card_html(
            "Orchestrator · Protocol", "Commander",
            "Convene the council, run the round protocol, draft numbered plans, and gate approval "
            "on the deterministic validator and four matching votes.",
            f"Controller · v{cur_version}", f"{plan.risk if plan else '—'}",
            pill(status, status_kind(status)), cmd_msgs, cmd_round,
        ),
        unsafe_allow_html=True,
    )

    st.write("")
    dept_cols = st.columns(4)
    for col, dept in zip(dept_cols, MODE_TABLE):
        role = Role(dept)
        profile = ROLE_PROFILES[role]
        selected = plan.selections.get(dept) if plan else None
        mode_txt = f"{selected} {MODES[selected].kind}" if selected else "—"
        risk_txt = str(MODES[selected].risk) if selected else "—"
        vote = _latest_vote(engine, role, cur_version)
        if vote is not None:
            vote_html = (
                pill(vote.decision.value, "ok" if vote.decision.value == "ACCEPT" else "danger")
                + f'<span class="ares-pill pill-{engine_pill_kind(vote.engine)}">'
                f'{engine_badge(vote.engine)}</span>'
            )
        else:
            vote_html = '<span class="ares-legend">no vote yet on this version</span>'
        with col:
            st.markdown(
                agent_card_html(
                    "Department agent", dept, profile.goal, mode_txt, risk_txt, vote_html,
                    counts.get(role, 0), last_round.get(role, 0),
                ),
                unsafe_allow_html=True,
            )

# --- Transcript ------------------------------------------------------------
with tab_transcript:
    section("Council transcript")
    st.markdown(
        '<div class="ares-legend">'
        f'{pill("🟢 LIVE AI", "live")} real model output &nbsp; '
        f'{pill("🔴 FALLBACK", "fallback")} deterministic policy &nbsp; '
        f'{pill("⚙️ SYSTEM", "system")} Commander / validator. '
        'Fallback is never shown as live AI.</div>',
        unsafe_allow_html=True,
    )
    if not engine.transcript:
        st.info("No messages yet.")
    else:
        st.caption(
            f"Provenance: **{n_llm}** LIVE AI · **{n_fb}** FALLBACK · **{n_sys}** SYSTEM "
            f"({len(engine.transcript)} messages total)."
        )
        df = transcript_rows(engine.transcript)
        if not df.empty:
            df = df.assign(time=df["timestamp"].map(
                lambda t: datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S")
            ))
        fcol1, fcol2, fcol3 = st.columns(3)
        scen = fcol1.multiselect("Scenario", sorted(df["scenario"].unique()),
                                 default=list(df["scenario"].unique()))
        typ = fcol2.multiselect("Message type", sorted(df["type"].unique()),
                                default=list(df["type"].unique()))
        eng = fcol3.multiselect("Source", sorted(df["engine"].unique()),
                                default=list(df["engine"].unique()))
        view = df[df["scenario"].isin(scen) & df["type"].isin(typ) & df["engine"].isin(eng)]
        show_cols = ["time", "scenario", "round", "plan_version", "sender",
                     "recipient", "type", "source", "content"]
        st.dataframe(view[show_cols], width="stretch", hide_index=True)
        st.download_button(
            "⬇️ Download transcript CSV",
            view[show_cols].to_csv(index=False).encode("utf-8"),
            file_name="council_transcript.csv", mime="text/csv",
        )

        section("Full messages")
        for m in engine.transcript:
            icon = {"PROPOSAL": "📝", "OBJECTION": "⚠️", "COUNTEROFFER": "↩️",
                    "COMMITMENT": "🤝", "VOTE": "🗳️", "APPROVAL": "✅",
                    "INFEASIBLE": "⛔", "STATE": "📣", "INFO": "ℹ️"}.get(m.type.value, "•")
            ts = datetime.datetime.fromtimestamp(m.timestamp).strftime("%H:%M:%S")
            title = (
                f"{icon} {engine_badge(m.engine)} · R{m.round} · v{m.plan_version} · "
                f"{m.sender.value} → {m.recipient.value if m.recipient else 'Council'} · "
                f"{m.type.value} · {ts}"
            )
            with st.expander(title):
                st.markdown(message_block(m.content), unsafe_allow_html=True)
                if m.payload:
                    st.json(m.payload)

# --- Mode Board ------------------------------------------------------------
with tab_modes:
    section("Department mode board")
    consequence = {
        "Standard": "Protects the full mission.",
        "Restricted": "Completes the immediate task but creates recovery debt.",
        "Sacrifice": "Keeps the department alive but accepts serious long-term damage.",
    }
    for dept in MODE_TABLE:
        profile = ROLE_PROFILES[Role(dept)]
        selected = plan.selections.get(dept) if plan else None
        with st.container(border=True):
            cols = st.columns([2, 2, 4])
            cols[0].markdown(f"**{dept}**  \nGoal: {profile.goal}")
            if selected:
                mode = MODES[selected]
                kind = "danger" if mode.is_sacrifice else ("warn" if mode.kind == "Restricted" else "ok")
                cols[1].markdown(
                    f"**{selected} {mode.kind}** {pill(mode.kind, kind)}  \nRisk {mode.risk}  \n{mode.describe()}",
                    unsafe_allow_html=True,
                )
                cols[2].markdown(
                    consequence.get(mode.kind, "") +
                    (f"  \n**Loss:** {profile.sacrifice_loss}." if mode.is_sacrifice else "")
                )
            else:
                cols[1].markdown("_No mode selected_")
            options = " | ".join(
                f"{m.mode_id} {m.kind} (P{m.power}/W{m.water}/O{m.oxygen}/R{m.robot}/B{m.bandwidth}, risk {m.risk})"
                for m in modes_for(dept).values()
            )
            cols[2].caption("Options: " + options)
            if plan:
                rets = plan.accepted_commitments_for(dept)
                if rets:
                    st.markdown(
                        "**Compensation commitments:** "
                        + "; ".join(f"{c.owner.value} → {c.return_term}" for c in rets)
                    )
                if selected and MODES[selected].is_sacrifice and len(rets) < 2:
                    st.error("Sacrifice without two accepted returns — validator will block this.")

# --- Validation ------------------------------------------------------------
with tab_validation:
    section("Deterministic validator")
    if plan and plan.validation:
        v = plan.validation
        if v.passed:
            st.success(f"✅ PASS — plan v{v.plan_version} satisfies every deterministic check.")
        else:
            st.error(f"❌ FAIL — plan v{v.plan_version} is blocked. See reasons below.")
        checks = [{"Check": c.name, "Result": "✅ PASS" if c.passed else "❌ FAIL", "Reason": c.reason}
                  for c in v.checks]
        st.dataframe(pd.DataFrame(checks), width="stretch", hide_index=True)
        used = " / ".join(f"{k[0].upper()}{v.totals.get(k, 0)}" for k in RESOURCE_ORDER)
        avail = " / ".join(f"{k[0].upper()}{engine.state.resources.get(k, 0)}" for k in RESOURCE_ORDER)
        st.caption(
            f"Used {used}  ≤  Available {avail}   |   risk {v.risk} ≤ {engine.state.risk_limit}   |   "
            f"sacrifice {len(v.sacrifices)} ≤ {engine.state.max_sacrifices}"
        )
    else:
        st.info("No plan to validate yet.")
    if engine.outcomes and engine.outcomes[-1].infeasibility:
        st.error("INFEASIBLE — required change: "
                 + engine.outcomes[-1].infeasibility["required_policy_change"])
        st.json(engine.outcomes[-1].infeasibility)

# --- Plans & Votes ---------------------------------------------------------
with tab_plans:
    section("Plan versions")
    if engine.commander.plans:
        pv = [{"Version": p.version, "Scenario": p.scenario_id, "Round": p.created_round,
               "Status": p.status.value, "Risk": p.risk,
               "Sacrifice": ", ".join(p.sacrifices) or "none",
               "Selections": ", ".join(f"{d}={m}" for d, m in p.selections.items()),
               "Note": p.note} for p in engine.commander.plans]
        st.dataframe(pd.DataFrame(pv), width="stretch", hide_index=True)
        st.markdown(
            f'{pill("STALE", "warn")} superseded but still valid under old rules &nbsp; '
            f'{pill("INVALID", "danger")} no longer valid after the emergency',
            unsafe_allow_html=True,
        )

    section("Votes")
    if engine.commander.all_votes:
        vv = pd.DataFrame([v.to_row() for v in engine.commander.all_votes])
        if "engine" in vv.columns:
            vv["source"] = vv["engine"].map(engine_badge)
        st.dataframe(vv, width="stretch", hide_index=True)
    else:
        st.caption("No votes cast yet.")

    section("Commitments")
    if engine.commander.commitments:
        st.dataframe(pd.DataFrame([c.to_row() for c in engine.commander.commitments]),
                     width="stretch", hide_index=True)
    else:
        st.caption("No commitments yet.")

    if engine.crisis_log:
        section("Crisis handling log")
        st.dataframe(pd.DataFrame([{
            "event": e["event"]["event_name"], "status": e["outcome_status"],
            "rounds": e["rounds_used"], "new_plan_version": e["new_plan_version"],
            "detail": e["outcome_detail"],
        } for e in engine.crisis_log]), width="stretch", hide_index=True)

    if engine.final_plan and engine.final_plan.status == PlanStatus.APPROVED:
        st.success(
            f"✅ Final approved plan: v{engine.final_plan.version} "
            f"(risk {engine.final_plan.risk}, sacrifice {', '.join(engine.final_plan.sacrifices) or 'none'})"
        )
