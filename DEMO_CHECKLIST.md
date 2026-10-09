# ARES Accord — Demo Checklist (3–5 minute video)

## Pre-flight (do this before recording)
- [ ] `python -m pip install -r requirements.txt`
- [ ] Decide the mode you will demo and set it in the sidebar **Agent mode** control:
  - **🔒 OFFLINE (safest, recommended for judging):** the default. No key needed.
  - **🌐 LIVE:** add `GEMINI_API_KEY` or `OPENAI_API_KEY` to `.env`, then pick LIVE.
    If a call fails or is rate-limited the agent visibly **FALLS BACK** (🔴).
- [ ] Confirm the prominent **Agent mode** banner shows OFFLINE or LIVE at the top.
- [ ] `python -m streamlit run app.py` opens without errors.
- [ ] `python -m pytest -q` prints `83 passed`.
- [ ] `.env` is NOT committed (`git status` should not list it).

## Recording script

### A. Baseline negotiation (~90 s)
1. Show the sidebar: resource pool = 79 / 52 / 59 / 26 / 17, risk limit 24.
2. Click **Start / restart crisis**.
3. In **Council Transcript**, point out:
   - Round 1 proposals (all request Standard).
   - Round 2 objections and an explicit **sacrifice refusal**.
   - Round 3 two **accepted return commitments** for the sacrificing department.
   - Round 4 four **ACCEPT** votes on the same plan version.
4. In **Validation**, show the plan **PASS** with per-check reasons and totals vs pool.
5. In **Mission Control**, show the selected modes and remaining reserve.

### B. Crisis injection + recovery (~120 s)
6. Sidebar → select **Solar Aftershock (Power -4)** → **Inject event & renegotiate**.
7. Show the emergency banner, then in **Plans & Votes** that the old approved plan
   is now **STALE/INVALID** and prior commitments **EXPIRED**.
8. Show **Crisis Override active** (risk 28, up to 2 sacrifices) and the new
   approved plan version with fresh votes.
9. (Optional) Paste this exact JSON into the event box and press
   **Inject event & renegotiate** to show a justified **INFEASIBLE** result
   (note: an empty `resource_deltas` would stay APPROVED, so the deltas matter):

   ```json
   {
     "event_id": "CRISIS_999",
     "event_name": "Catastrophic Cascade",
     "description": "Multiple systems fail; the resource pool collapses.",
     "resource_deltas": {"power": -20, "water": -20, "oxygen": -20, "robot": -20, "bandwidth": -20}
   }
   ```
   The status turns **INFEASIBLE** and the Validation tab shows the exact
   required policy change.

### C. Exports (~30 s)
10. Exports are written to `./exports/` automatically after a run. In the
    **3. Export** sidebar section, click the download buttons (or open the
    files directly): `final_allocation.json` and `council_transcript.csv`.
11. (Optional) Click **Reset everything** to clear the council and return to
    the idle state for a fresh run.

## Rules you can point at on screen
- [ ] ≥ 3 rounds before first approval → visible round numbers.
- [ ] ≥ 1 rejected/revised proposal → plan v1/v2 FAIL.
- [ ] ≥ 1 sacrifice refusal → Round 2 objection message.
- [ ] 2 accepted returns → Commitments table, all `ACCEPTED`.
- [ ] 4 ACCEPT votes on one version → Votes table.
- [ ] Emergency recovery in ≤ 3 minutes → new approved plan or INFEASIBLE.

## After recording
- [ ] `exports/final_allocation.json` present.
- [ ] `exports/council_transcript.json` and `.csv` present.
- [ ] `exports/crisis_handling_log.json` present.
- [ ] `exports/llm_operations_log.json` present (model calls, redacted errors).
- [ ] `docs/architecture_diagram.png` present.
