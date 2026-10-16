"""Alarm agent: pure code, no LLM. Makes silence loud — a stalled pipeline or a
red CI gets a GitHub Issue on the project. All project facts come from profile.
"""
from __future__ import annotations

import json
from datetime import datetime

from .core import now_iso, profile_of, run, save_state, state_of

MAX_SILENCE_DAYS = 2
MAX_CONSECUTIVE_FAILURES = 3
ISSUE_FINGERPRINT = "dev-team pipeline needs attention"


def _issue_list(slug: str) -> list[str]:
    r = run(["gh", "issue", "list", "-R", slug, "--state", "open",
             "--json", "title"], None, timeout=60)
    if r.returncode != 0:
        return []
    return [t.get("title", "") for t in json.loads(r.stdout or "[]")]


def file_issue(slug: str, title: str, body: str) -> tuple[bool, str]:
    r = run(["gh", "issue", "create", "-R", slug, "--title", title, "--body", body],
            None, timeout=90)
    return r.returncode == 0, (r.stdout + r.stderr)[:500]


def check(project, log) -> int:
    profile = profile_of(project)
    slug = profile["repo_slug"]
    state = state_of(project)
    problems: list[str] = []

    last_ok = state.get("last_success_date")
    safe_left = any(t.get("risk", "safe") == "safe" for t in state.get("remaining", []))
    if safe_left and last_ok:
        days = (datetime.now() - datetime.strptime(last_ok, "%Y-%m-%d")).days
        if days > MAX_SILENCE_DAYS:
            problems.append(
                f"No successful pipeline run for {days} days (last: {last_ok}) while the "
                f"queue has {len(state.get('remaining', []))} surfaces left. The nightly "
                "orchestrator may have stopped — check `launchctl list | grep devteam` "
                "and dev-team/logs/.")
    elif safe_left and not last_ok:
        problems.append("Pipeline has never succeeded while the queue has work remaining. "
                        "Check dev-team/logs/ for failing runs.")

    streak = sum(1 for r in reversed(state.get("runs", []))
                 if r.get("result") in ("failed", "error"))
    if streak >= MAX_CONSECUTIVE_FAILURES:
        problems.append(f"{streak} consecutive failed runs — inspect dev-team/logs "
                        "before assuming the tasks are fine.")

    last = state.get("last_run", {})
    if last.get("pushed_sha") and last.get("ci", {}).get("status") == "pending":
        r = run(["gh", "run", "list", "-R", slug, "-L", "5",
                 "--json", "status,conclusion,headSha"], None, timeout=60)
        if r.returncode == 0:
            for e in json.loads(r.stdout or "[]"):
                if e.get("headSha") == last["pushed_sha"] and e["status"] == "completed":
                    state["last_run"]["ci"] = {"status": "completed",
                                               "conclusion": e["conclusion"]}
                    save_state(project, state)
                    if e["conclusion"] != "success":
                        problems.append("CI FAILED on the last pushed release commit "
                                        f"({last['pushed_sha'][:8]}) — internal testing was "
                                        "NOT updated. Inspect the Actions run.")
                    break

    if not problems:
        log("alarm: healthy")
        return 0

    body = "\n\n".join(f"- {p}" for p in problems)
    open_titles = _issue_list(slug)
    if open_titles and any(ISSUE_FINGERPRINT in t for t in open_titles):
        for p in problems:
            log("  ALARM: " + p)
        log("alarm: a dev-team issue is already open (dedupe)")
        return 1
    title = ISSUE_FINGERPRINT
    if any("CI FAILED" in p for p in problems):
        title = "[dev-team] CI failure — internal-testing delivery blocked"
    ok, out = file_issue(slug, title, body + f"\n\n_detected {now_iso()} by dev-team alarm_")
    for p in problems:
        log("  ALARM: " + p)
    log("alarm: filed GitHub Issue" if ok else f"alarm: could not file issue: {out}")
    return 1
