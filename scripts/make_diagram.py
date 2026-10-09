"""Generate docs/architecture_diagram.png (run: python scripts/make_diagram.py)."""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "architecture_diagram.png")

NAVY = "#0b2545"
BLUE = "#1b6ca8"
LIGHT = "#eaf2fb"
ORANGE = "#e07a1f"
GREEN = "#2e7d32"
GREY = "#5b6770"


def box(ax, x, y, w, h, text, fc=LIGHT, ec=BLUE, fs=9, tc=NAVY):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.06",
                                linewidth=1.6, edgecolor=ec, facecolor=fc))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            color=tc, wrap=True)


def arrow(ax, p1, p2, color=GREY, style="-|>", lw=1.5, rad=0.0):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, color=color, lw=lw,
                                 mutation_scale=12,
                                 connectionstyle=f"arc3,rad={rad}"))


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig, ax = plt.subplots(figsize=(13, 8))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 8)
    ax.axis("off")

    ax.text(6.5, 7.6, "ARES Accord — Multi-Agent Resource Negotiation Architecture",
            ha="center", fontsize=15, fontweight="bold", color=NAVY)

    # Dashboard
    box(ax, 4.0, 6.6, 5.0, 0.75, "Streamlit Dashboard\nMission Control · Transcript · Mode Board · Validation · Judge Controls",
        fc="#fff3e0", ec=ORANGE, fs=9, tc=ORANGE)

    # Commander
    box(ax, 4.3, 5.3, 4.4, 0.8, "Commander / Orchestrator\nrounds · plan versions · candidate selection · approval gate",
        fc=NAVY, ec=NAVY, fs=9, tc="white")

    # Shared state
    box(ax, 4.3, 4.1, 4.4, 0.75, "Shared Crisis State\nresources · risk limit · sacrifice limit · events · plan vN",
        fc="#e3f2fd", ec=BLUE, fs=9)

    # Department agents
    depts = ["Life Support\nL1/L2/L3", "Medical\nM1/M2/M3",
             "Food Production\nF1/F2/F3", "Engineering\nE1/E2/E3"]
    for i, d in enumerate(depts):
        box(ax, 0.4, 3.5 - i * 0.95, 2.4, 0.7, d, fc="#e8f5e9", ec=GREEN, fs=8.5, tc=GREEN)
        arrow(ax, (2.8, 3.85 - i * 0.95), (4.3, 4.45), color=GREEN, rad=0.15)
        arrow(ax, (4.3, 4.3), (2.8, 3.7 - i * 0.95), color=GREEN, rad=0.15)

    # Right column: validator, solver
    box(ax, 9.4, 5.2, 3.2, 0.8, "Deterministic Validator\nmodes · resources · risk · returns · votes",
        fc="#fdecea", ec="#c0392b", fs=9, tc="#c0392b")
    box(ax, 9.4, 4.0, 3.2, 0.8, "Deterministic Solver\nall 81 mode combinations",
        fc="#ede7f6", ec="#5e35b1", fs=9, tc="#5e35b1")
    box(ax, 9.4, 2.8, 3.2, 0.8, "LLM Adapter\nGemini / OpenAI / labelled fallback",
        fc="#fff8e1", ec="#f9a825", fs=9, tc="#8d6e00")

    arrow(ax, (8.7, 5.9), (9.4, 5.6), color="#c0392b")
    arrow(ax, (9.4, 5.2), (8.7, 5.5), color="#c0392b")
    arrow(ax, (8.7, 5.45), (9.4, 4.4), color="#5e35b1")
    arrow(ax, (9.4, 4.4), (8.7, 5.3), color="#5e35b1")
    arrow(ax, (2.8, 3.5), (9.4, 3.2), color="#f9a825", rad=-0.25)

    # Storage
    box(ax, 4.3, 1.6, 4.4, 0.75, "Storage / Exports\nfinal_allocation.json · council_transcript.json & CSV · crisis log",
        fc="#eceff1", ec=GREY, fs=8.5, tc=GREY)
    arrow(ax, (6.5, 4.1), (6.5, 2.35), color=GREY)
    arrow(ax, (4.0, 6.6), (4.0, 6.1), color=ORANGE)
    arrow(ax, (8.0, 6.1), (8.0, 6.6), color=ORANGE)

    ax.text(0.4, 0.7, "Solid arrows: messages & data flow.   "
                      "Green: department agents (independent state, goals, votes).",
            fontsize=8, color=GREY)
    fig.savefig(OUT, dpi=200, bbox_inches="tight")
    print("wrote", os.path.abspath(OUT))


if __name__ == "__main__":
    main()
