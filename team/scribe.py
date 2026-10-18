"""Human-readable mirror generation. state.json is the source of truth; these
markdown files are conveniences that are safe if stale."""
from __future__ import annotations

from pathlib import Path

from .core import pipeline_dir, state_of, today


def update(project: Path) -> None:
    state = state_of(project)
    pdir = pipeline_dir(project)
    lines = [
        "# dev-team — pipeline progress mirror",
        f"_Regenerated {today()} from .pipeline/state.json — this file may lag; state.json is truth._",
        "",
    ]
    shipped = state.get("shipped", [])
    if shipped:
        lines += ["## Shipped (via version bump → CI → internal testing)", ""]
        for s in shipped:
            lines.append(f"- **{s.get('version','?')}** — {s.get('label')} "
                         f"({s.get('date')}, commit {str(s.get('commit'))[:8]})")
        lines.append("")
    runs = state.get("runs", [])
    if runs:
        lines += ["## Run ledger", ""]
        for r in runs[-30:]:
            lines.append(f"- {r.get('date')} · {r.get('task_id','—')} · "
                         f"{r.get('result','—')} · {r.get('note','')[:140]}")
        lines.append("")
    remaining = state.get("remaining", [])
    if remaining:
        lines += ["## Remaining surfaces", ""]
        for t in remaining:
            risk = t.get("risk", "safe")
            lines.append(f"- {t.get('label')} ({'⚠️ supervised only' if risk == 'supervised' else 'safe'})")
        lines.append("")
    upgrades = state.get("upgrades", [])
    if upgrades:
        lines += ["## Upgrades available (advisory — pipeline works without them)", ""]
        for u in upgrades:
            lines.append(f"- **{u.get('title')}** — {u.get('benefit')}")
            lines.append(f"  how: {u.get('how')}")
        lines.append("")
    header = state.get("last_run", {})
    if header:
        lines += ["## Last run", "",
                  f"- {header.get('finished')} · result: {header.get('result')} · "
                  f"CI: {header.get('ci')}"]
    (pdir / "progress.md").write_text("\n".join(lines) + "\n")
    # The goal tree view travels with the mirror (goals.txt + goals.html). A
    # render failure must never break bookkeeping — mirrors may go stale.
    try:
        from . import tree as tree_mod
        tree_mod.write(project)
    except Exception:
        pass
