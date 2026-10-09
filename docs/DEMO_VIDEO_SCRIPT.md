# ARES Accord — Demo Video Script (3–5 minutes)

A concise shot-by-shot script. It is written for the real dashboard; narrate what
is actually on screen and never claim a live model call succeeded unless the
badge says 🟢 LIVE AI.

**Target length:** ~4 minutes.

---

## 0. Pre-flight (off camera)
- `python -m pip install -r requirements.txt`
- `python -m streamlit run app.py` — confirm it opens with no error.
- Leave **Agent mode = 🔒 OFFLINE** for the main take (deterministic, no key
  needed). Optionally record a short LIVE add-on (Section D).
- Have the event JSON for Section B ready to paste.

---

## A. Introduction (~20 s)
> "ARES Accord is a multi-agent negotiation system for a Mars colony emergency.
> Five agents — a Commander plus Life Support, Medical, Food Production and
> Engineering — negotiate over one scarce resource pool. A deterministic Python
> validator checks every number; the AI only talks."

Point at the **Agent mode** banner: "We run in **🔒 OFFLINE** so the result is
reproducible and needs no API key."

## B. Baseline negotiation (~90 s)
1. Sidebar: show the pool **79 / 52 / 59 / 26 / 17**, risk limit **24**.
2. Click **🚀 Start / restart crisis**.
3. **Council Transcript** — walk the rounds:
   - Round 1 proposals (everyone asks for Standard).
   - Round 2 objections and an explicit **sacrifice refusal**.
   - Round 3 two **accepted return commitments** for the sacrificing department.
   - Round 4 four **ACCEPT** votes on the same plan version.
4. Point at the **provenance badges**: "Every message is tagged. In OFFLINE mode
   they are all **🔴 FALLBACK** — clearly labelled, never claimed as live AI."
5. **Validation** tab: plan **PASS**, per-check reasons, totals vs pool.
6. **Mission Control**: selected modes and the remaining reserve.

## C. Crisis injection + recovery (~90 s)
7. Sidebar → **Solar Aftershock (Power −4)** → **⚡ Inject event & renegotiate**.
8. Show the emergency banner, then **Plans & Votes**: the old approved plan is now
   **STALE/INVALID** and prior commitments **EXPIRED**.
9. Show **Crisis Override active** (risk 28, up to 2 sacrifices), then the new
   approved plan version with **fresh votes** on the new version.
10. (Optional) Paste the Catastrophic Cascade JSON (see `DEMO_CHECKLIST.md`) to
    show a justified **INFEASIBLE** result with the exact required policy change.
    Click **🧹 Reset everything**, then re-run if you want a clean final state.

## D. Exports (~20 s)
11. The **3. Export** sidebar section writes to `./exports/`. Open
    `final_allocation.json` and `council_transcript.csv` and show the real records.

## E. LIVE add-on (optional, ~30 s — only if a key is configured)
12. Change **Agent mode** to **🌐 LIVE**, click **Start / restart crisis**.
13. Show the banner flip to **🟢 LIVE** and the byte-level provenance: messages
    that the model produced show **🟢 LIVE AI**; any that fell back show
    **🔴 FALLBACK** and a warning states the reason.
14. If the provider rate-limits, say plainly: "It was rate-limited, so the agent
    fell back — that is the reliability path working, not a failure."

## F. Close (~15 s)
> "Validator-gated, provably resource-safe, and honest about what is real AI
> output versus deterministic fallback. That is ARES Accord."

---

## Do not do
- Do not describe 🔴 FALLBACK output as "the AI decided".
- Do not claim OpenAI works unless `OPENAI_API_KEY` was actually tested.
- Do not claim live Gemini success unless a real request returned 🟢 LIVE AI.
