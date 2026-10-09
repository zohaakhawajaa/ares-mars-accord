"""Headless dashboard smoke test using Streamlit's AppTest framework."""

import os

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))


def _run_app():
    return AppTest.from_file(APP_PATH, default_timeout=180)


def _button(at, label_fragment: str):
    for btn in at.sidebar.button:
        if label_fragment.lower() in btn.label.lower():
            return btn
    raise AssertionError(f"no sidebar button matching {label_fragment!r}")


def _main_button(at, label_fragment: str):
    for btn in at.button:
        if label_fragment.lower() in btn.label.lower():
            return btn
    raise AssertionError(f"no main-area button matching {label_fragment!r}")


def test_dashboard_renders_without_error():
    at = _run_app()
    at.run()
    assert not list(at.exception)


def test_dashboard_start_and_event_injection():
    at = _run_app()
    at.run()
    assert not list(at.exception)

    # Start the baseline crisis.
    _button(at, "Start / restart").click().run()
    assert not list(at.exception)
    engine = at.session_state.engine
    assert engine.state.status == "APPROVED"

    # Inject a judge event and confirm recovery.
    event = '{"event_id":"CRISIS_X","event_name":"Smoke","impact":{"power_reduction":4}}'
    at.sidebar.text_area[0].set_value(event)
    _button(at, "Inject event").click().run()
    assert not list(at.exception)
    engine = at.session_state.engine
    assert engine.crisis_log, "crisis log should record the injected event"
    assert engine.outcomes[-1].status in ("APPROVED", "INFEASIBLE", "DEADLOCK")


def test_dashboard_snapshot_reflects_runtime_state():
    at = _run_app()
    at.run()
    _button(at, "Start / restart").click().run()
    engine = at.session_state.engine

    snap = engine.snapshot()
    plan = engine.commander.current_plan
    assert plan is not None
    # Dashboard numbers must come from the live plan, not hardcoded constants.
    assert snap["used"] == plan.totals
    assert snap["plan"]["risk"] == plan.risk
    # Runtime pool must match the single source of truth.
    from ares.packages import DEFAULT_RESOURCES

    assert snap["available"] == DEFAULT_RESOURCES


def test_dashboard_reset_clears_everything():
    at = _run_app()
    at.run()
    _button(at, "Start / restart").click().run()
    assert at.session_state.engine.started is True
    assert at.session_state.engine.transcript

    _button(at, "Reset everything").click().run()
    assert not list(at.exception)
    fresh = at.session_state.engine
    assert fresh.started is False
    assert not fresh.transcript
    assert not fresh.crisis_log
    assert fresh.outcomes == []


def test_dashboard_writes_exports_after_run():
    at = _run_app()
    at.run()
    _button(at, "Start / restart").click().run()
    assert not list(at.exception)
    assert os.path.isdir("exports"), "exports directory should be created"
    assert os.listdir("exports"), "exports directory should contain run artifacts"


def test_dashboard_human_review_flow():
    at = _run_app()
    at.run()
    # Enable the opt-in human approval gate (the only sidebar toggle).
    at.sidebar.toggle[0].set_value(True).run()
    _button(at, "Start / restart").click().run()
    assert not list(at.exception)

    engine = at.session_state.engine
    assert engine.human_review_pending is True

    _main_button(at, "Approve as human").click().run()
    assert not list(at.exception)
    engine = at.session_state.engine
    assert engine.state.status == "APPROVED"
    assert engine.final_plan.status.value == "APPROVED"


def test_dashboard_defaults_to_offline_and_labels_fallback():
    at = _run_app()
    at.run()
    # The mode control exists and the default choice is OFFLINE.
    assert at.sidebar.radio, "an explicit agent-mode control must be present"
    assert at.sidebar.radio[0].value.startswith("🔒")

    _button(at, "Start / restart").click().run()
    assert not list(at.exception)
    engine = at.session_state.engine
    assert engine.llm.available is False, "default run must be offline/deterministic"
    assert engine.state.status == "APPROVED"

    agent_msgs = [m for m in engine.transcript if m.sender.value != "Commander"]
    assert agent_msgs
    assert all(m.engine == "fallback" for m in agent_msgs)
    assert all(m.engine != "llm" for m in engine.transcript), "offline is never labelled live"


def test_dashboard_live_selection_without_a_key_falls_back_honestly():
    at = _run_app()
    at.run()
    # Choose the LIVE option. With no usable key in the test environment the
    # client resolves to the deterministic path and the run must still finish,
    # with every message honestly labelled (never as live AI).
    live_option = at.sidebar.radio[0].options[1]
    at.sidebar.radio[0].set_value(live_option).run()
    _button(at, "Start / restart").click().run()
    assert not list(at.exception)
    engine = at.session_state.engine
    assert engine.state.status == "APPROVED"
    assert all(m.engine != "llm" for m in engine.transcript), "no fake live labels"


def test_dashboard_preset_syncs_json_and_injects():
    at = _run_app()
    at.run()
    _button(at, "Start / restart").click().run()

    # Changing the practice-event dropdown must update the JSON box (regression
    # guard: a keyed text_area must not keep a stale event).
    at.sidebar.selectbox[0].set_value(at.sidebar.selectbox[0].options[1]).run()
    assert "Water Contamination" in at.sidebar.text_area[0].value

    # Inject the selected event through the UI and confirm recovery.
    _button(at, "Inject event").click().run()
    assert not list(at.exception)
    engine = at.session_state.engine
    assert engine.state.override_active is True
    assert engine.outcomes[-1].status == "APPROVED"
