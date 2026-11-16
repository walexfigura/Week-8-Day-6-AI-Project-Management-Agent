"""Optional PNG charts (requires `pip install matplotlib`). Reports work without them."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

STATUS_COLORS = {"Done": "#2e7d32", "In Progress": "#1565c0", "Not Started": "#9e9e9e", "Blocked": "#c62828",
                 "Waiting": "#ef6c00", "At Risk": "#f9a825"}


def render_charts(health: dict[str, Any], out_dir: Path, stamp: str) -> tuple[list[str], str | None]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return [], "matplotlib is not installed; skipped charts (pip install matplotlib)."
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    counts = health["metrics"]["status_counts"]
    fig, ax = plt.subplots(figsize=(6, 3))
    labels = list(counts)
    ax.barh(labels, [counts[k] for k in labels], color=[STATUS_COLORS.get(k, "#607d8b") for k in labels])
    ax.set_xlabel("Tasks")
    ax.set_title("Task status distribution")
    for i, v in enumerate(counts[k] for k in labels):
        ax.text(v + 0.05, i, str(v), va="center")
    fig.tight_layout()
    p = out_dir / f"status-{stamp}.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(str(p))

    rows = [(tid, pr) for tid, pr in health["projections"].items() if pr["status"] != "Done"]
    fig, ax = plt.subplots(figsize=(8, 0.35 * len(rows) + 1.2))
    for i, (_tid, pr) in enumerate(rows):
        due = date.fromisoformat(pr["due_date"]).toordinal()
        fin = date.fromisoformat(pr["projected_finish"]).toordinal()
        ax.plot([due], [i], marker="|", markersize=14, color="#333")
        ax.plot([fin], [i], marker="o", color="#c62828" if pr["slack_workdays"] < 0 else "#1565c0")
    ax.set_yticks(range(len(rows)), [t for t, _ in rows])
    ticks = sorted({date.fromisoformat(pr["due_date"]).toordinal() for _, pr in rows})
    ax.set_xticks(ticks, [date.fromordinal(t).strftime("%b %d") for t in ticks], rotation=45, ha="right", fontsize=7)
    ax.invert_yaxis()
    ax.set_title("Due date (|) vs projected finish (o; red = late)")
    fig.tight_layout()
    p = out_dir / f"timeline-{stamp}.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(str(p))
    return paths, None
