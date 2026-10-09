"""Generate submission evidence logs (offline, deterministic).

Run: python scripts/generate_evidence.py
Writes evidence/ with an opening negotiation log, a post-event log, the final
plan files and the crisis handling log.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ares.engine import AresEngine
from ares.llm import LLMClient

OUT = os.path.join(os.path.dirname(__file__), "..", "evidence")


def main():
    os.makedirs(OUT, exist_ok=True)
    engine = AresEngine(llm=LLMClient(provider="fallback"))

    opening = engine.start()
    opening_status = {
        "status": opening.status,
        "rounds": opening.rounds,
        "process": opening.process,
        "plan_version": opening.plan.version if opening.plan else None,
    }

    # Post-event: the documented "Solar Aftershock" practice event.
    event = {
        "event_id": "CRISIS_002",
        "event_name": "Solar Aftershock",
        "description": "Secondary solar flare. Power generation drops by a small margin.",
        "impact": {"power_reduction": 4},
        "requires_replan": True,
        "message_to_commander": "ALERT: Solar Aftershock detected. Council must reconvene.",
    }
    post = engine.inject_event(event)
    post_status = {
        "status": post.status,
        "rounds": post.rounds,
        "process": post.process,
        "override_active": engine.state.override_active,
        "plan_version": post.plan.version if post.plan else None,
    }

    paths = engine.export(OUT)
    with open(os.path.join(OUT, "opening_negotiation.json"), "w", encoding="utf-8") as fh:
        json.dump(opening_status, fh, indent=2)
    with open(os.path.join(OUT, "post_event_negotiation.json"), "w", encoding="utf-8") as fh:
        json.dump(post_status, fh, indent=2)

    print("Evidence written:")
    for name in sorted(os.listdir(OUT)):
        print("  evidence/" + name)
    print("Opening:", opening_status)
    print("Post-event:", post_status)


if __name__ == "__main__":
    main()
