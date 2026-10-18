"""Research-agent for new features — a HYBRID code agent.

Deterministic collectors (no LLM): GA4 metrics via service account,
codebase surface scan, repo/release facts, stage status → all into
.pipeline/research.json.

Then exactly ONE LLM synthesis call: decide the prioritized next-task
queue from the research. Output is strict JSON, validated by code —
"LLM proposes, code validates and applies" (same pattern as the outreach
pipeline's ledgers). With --no-llm it stops after collection and the
calling agent reads the raw research itself.
"""
from __future__ import annotations

import json
import re
import subprocess  # noqa: F401
from pathlib import Path

from . import scan
from .core import (RunLog, now_iso, pipeline_dir, profile_of, run, save_json,
                   save_state, state_of)
from . import goals
from . import queue as queue_mod

MAX_QUEUE = 6


# --------------------------------------------------------------------------- #
# collectors
# --------------------------------------------------------------------------- #

def _ci_pull(project: Path, profile: dict) -> dict:
    """Recent runs of the project's delivery workflow. A red delivery run is
    the product's fire: research must see it and escalate restoring delivery
    to the highest priority (owner decision 2026-10-16 — self-healing loop)."""
    wf = profile.get("ci_workflow")
    slug = profile.get("repo_slug")
    out: dict = {"workflow": wf, "status": "unknown"}
    if not wf or not slug:
        out["reason"] = "profile lacks ci_workflow/repo_slug"
        return out
    r = run(["gh", "run", "list", "-R", slug, "--workflow", wf, "-L", "5",
             "--json", "databaseId,status,conclusion,headSha,event,createdAt"],
            project, timeout=60)
    if r.returncode != 0:
        return {"workflow": wf, "status": "unavailable",
                "reason": (r.stdout + r.stderr)[:200]}
    runs = json.loads(r.stdout or "[]")
    out["runs"] = runs[:5]
    if runs:
        out["last_conclusion"] = runs[0].get("conclusion")
        if runs[0].get("conclusion") == "failure":
            v = run(["gh", "run", "view", str(runs[0]["databaseId"]),
                     "--log-failed", "-R", slug], project, timeout=120)
            # keep the tail only — the interesting lines for an upload/policy
            # rejection live right above the error line
            out["failure_log_tail"] = (v.stdout + v.stderr)[-1500:]
    return out


def _ga4_pull(project: Path, profile: dict) -> dict:
    """GA4 via service account — the documented runReport pattern. The project
    DECLARES its connection in profile['connections']['ga4']
    {property, key_file?, exclude_device_models?} — team code holds no paths.
    Degrades loudly: status=unavailable + reason. Never prints the token."""
    conf = (profile.get("connections") or {}).get("ga4") or {}
    prop = conf.get("property")
    if not prop:
        return {"status": "unavailable", "reason":
                "project has not declared connections.ga4.property — "
                "run `devteam doctor` and answer the owner-ask"}
    key_src = conf.get("key_file", "")
    key = Path(key_src).expanduser() if key_src else None
    if key_src and key and key.exists():
        # per-project service-account key file (declared per project in its profile)
        try:
            account = json.loads(key.read_text()).get("client_email", "")
        except Exception:
            return {"status": "unavailable", "reason": f"unreadable key file {key_src}"}
        run(["gcloud", "auth", "activate-service-account", "--key-file", str(key)],
            project, timeout=120)
        tok = run(["gcloud", "auth", "print-access-token", "--account", account,
                   "--scopes", "https://www.googleapis.com/auth/analytics.readonly"],
                  project, timeout=120)
        token = (tok.stdout or "").strip()
        if tok.returncode != 0 or not token:
            return {"status": "unavailable", "reason": "gcloud SA token failed"}
    else:
        # simple default (owner decision 2026-10-16): ONE shared Google login for
        # all projects — `gcloud auth application-default login` once on this
        # machine; every project then declares only its GA4 property id, and
        # read access follows the owner's account exactly.
        tok = run(["gcloud", "auth", "application-default", "print-access-token",
                   "--scopes", "https://www.googleapis.com/auth/analytics.readonly"],
                  project, timeout=60)
        if tok.returncode != 0 or not (tok.stdout or "").strip():
            # login without the analytics scope consented — try the default set
            tok = run(["gcloud", "auth", "application-default", "print-access-token"],
                      project, timeout=60)
        token = (tok.stdout or "").strip()
        if tok.returncode != 0 or not token:
            return {"status": "unavailable", "reason":
                    "GA property declared but no owner auth yet: run "
                    "`gcloud auth application-default login --scopes=https://www.googleapis.com/auth/analytics.readonly` "
                    "once (shared across all projects), or place a SA key at connections.ga4.key_file"}

    try:

        def query(dims: list[str], metrics: list[str], days: int,
                  event_filter: str | None = None) -> dict | None:
            body: dict = {
                "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
                "dimensions": [{"name": d} for d in dims],
                "metrics": [{"name": m} for m in metrics],
            }
            excludes = conf.get("exclude_device_models") or []
            if excludes:
                body["dimensionFilter"] = {"andGroup": {"expressions": [
                    {"notExpression": {"filter": {"fieldName": "deviceModel",
                     "inListFilter": {"values": excludes}}}}]}}
            if event_filter:
                body["dimensionFilter"] = {"andGroup": {"expressions": [
                    {"filter": {"fieldName": "eventName",
                     "stringFilter": {"value": event_filter}}}]}}
                body["dimensionFilter"]["andGroup"]["expressions"].extend(
                    #  keep model exclusion alongside event filter
                    [{"notExpression": {"filter": {"fieldName": "deviceModel",
                      "inListFilter": {"values": excludes}}}}] if excludes else [])
            r = run(["curl", "-s", "-X", "POST",
                     "-H", f"Authorization: Bearer {token}",
                     "-H", "Content-Type: application/json",
                     f"https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport",
                     "-d", json.dumps(body)], project, timeout=120)
            if r.returncode != 0:
                return None
            try:
                d = json.loads(r.stdout)
                if "error" in d:
                    return {"error": d["error"].get("message", "?")[:200]}
                return d
            except json.JSONDecodeError:
                return None

        out: dict = {"status": "ok", "property": prop}
        u = query(["date"], ["newUsers", "activeUsers"], 7)
        if isinstance(u, dict) and "error" not in u and u.get("rows"):
            out["new_users_7d"] = sum(int(r["metricValues"][0]["value"]) for r in u["rows"])
            out["active_users_7d"] = sum(int(r["metricValues"][1]["value"]) for r in u["rows"])
        else:
            out["new_users_7d"] = None; out["ga4_error"] = (u or {}).get("error")
        un = query(["eventName"], ["eventCount"], 30, "app_remove")
        out["app_remove_events_30d"] = (
            sum(int(r["metricValues"][0]["value"]) for r in un["rows"])
            if isinstance(un, dict) and "rows" in un else None)
        cf = query(["eventName"], ["eventCount"], 30, "churn_feedback_reason_selected")
        out["churn_feedback_responses_30d"] = (
            sum(int(r["metricValues"][0]["value"]) for r in cf["rows"])
            if isinstance(cf, dict) and "rows" in cf else None)
        if out["churn_feedback_responses_30d"] == 0:
            out["churn_feedback_responses_30d"] = 0  # genuine zero: rows present, no value
        return out
    except Exception as e:  # degrade, never crash the nightly
        return {"status": "unavailable", "reason": str(e)[:200]}


def research_collect(project: Path, log: RunLog) -> dict:
    """All deterministic collectors → research dict (no LLM)."""
    profile = profile_of(project)
    state = state_of(project)
    surfaces = scan.scan_surfaces(project)
    remaining = [{"label": s["label"], "paths": s.get("targets", []),
                  "risk": s.get("risk", "safe")}
                 for s in surfaces if s["status"] == "unanimated"]
    latest = None
    r = run(["git", "-C", str(project), "ls-remote", "--tags", "origin", "refs/tags/v*"],
            project, timeout=60)
    versions = re.findall(r"refs/tags/v(\d+)\.(\d+)(?:\.(\d+))?$", r.stdout or "", re.M)
    if versions:
        latest = ".".join(max(versions, key=lambda v: tuple(int(x or 0) for x in v)))
    shipped_versions = sorted({s.get("version", "?") for s in state.get("shipped", [])})
    research = {
        "generated_at": now_iso(),
        "project": profile["name"],
        "signals": {
            "ga4": _ga4_pull(project, profile),
            "codebase": {
                "targets_total": len(profile["targets"]),
                "shipped_count": sum(1 for s in surfaces if s["status"] == "shipped"),
                "animated_count": sum(1 for s in surfaces if s["status"] == "animated"),
                "remaining": remaining,
            },
            "repo": {
                "latest_tag": latest,
                "shipped_versions_in_state": shipped_versions,
                "last_success_date": state.get("last_success_date"),
                "runs_recorded": len(state.get("runs", [])),
                "recent_runs": [
                    {"date": r.get("date"), "task_id": r.get("task_id"),
                     "result": r.get("result"), "note": (r.get("note") or "")[:300]}
                    for r in state.get("runs", [])[-8:]],
                "current_queue": state.get("queue", []),
                "queue_impact_log": state.get("queue_events", [])[-8:],
                "insights": state.get("insights", {}),
            },
            "stages": {
                "ci_workflow": profile.get("ci_workflow"),
                "conventions": len(profile.get("conventions", [])),
                "notes": profile.get("notes", ""),
            },
            "ci": _ci_pull(project, profile),
        },
        "constraints": {
            "frozen_paths_count": len(profile.get("frozen_paths", [])),
            "max_queue": MAX_QUEUE,
            "standing_decisions": profile.get("standing_decisions", []),
            "policy": ("respect the freeze: no new domain features without owner "
                       "sign-off; motion targets may only come from research."
                       "codebase.remaining; propose issues_to_file for anything "
                       "needing a human decision"),
        },
    }
    research["upgrades"] = capability_upgrades(research["signals"], profile, project)
    dest = project / ".pipeline" / "research.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(research, indent=2) + "\n")
    log(f"research: collected → {dest.name} "
        f"(ga4={research['signals']['ga4'].get('status')}, "
        f"remaining={len(remaining)}, upgrades={len(research['upgrades'])})")
    for u in research["upgrades"]:
        log(f"  upgrade available: {u['title']}")
    return research


# --------------------------------------------------------------------------- #
# one LLM synthesis call + validation
# --------------------------------------------------------------------------- #

PROMPT_TMPL = """You are the research/product agent for app "{project}".

{goals}

You are given research collected by deterministic code:
{research}

Decide what to BUILD NEXT. Output STRICT JSON only (no prose, no code fence):
{{
  "insights": {{"headline": "one line", "analysis": "2-5 sentences grounded in the numbers"}},
  "queue": [
    {{"type": "motion|bugfix|feature|investigation", "label": "...",
      "target": "<exact surface path for motion; short topic otherwise>",
      "priority": 1, "reason": "one line grounded in research",
      "advances": "<milestone id this task serves, or 'none'>",
      "supersedes": ["<exact label of an already-queued task this makes irrelevant>"]}}
  ],
  "off_goal": [
    {{"label": "<exact queue label you are keeping even though it advances nothing>", "reason": "one line: why it is still worth a run"}}
  ],
  "goal_changes": [
    {{"action": "add|park|set-status|retire|reparent",
      "id": "<add: optional id you want, so you can point queue entries at it;
              every action but add: the existing branch id>",
      "title": "<add: what this branch is for, in plain words>",
      "parent": "vision|<branch id>", "kind": "milestone|subgoal",
      "status": "active|pending|parked|done",
      "outcome": "<add: what will be true when this is done>",
      "measures": [{{"text": "..."}}],
      "why": "why the plan had to move — the owner reads exactly this"}}
  ],
  "milestone_verdict": {{"id": "<milestone id>", "met": true|false, "evidence": "grounded in the numbers/measures"}},
  "dropped": [
    {{"label": "<exact label of an already-queued task you are removing>", "reason": "one line why it is no longer queued"}}
  ],
  "issues_to_file": [
    {{"title": "...", "body": "what + why + recommended action + effort/risk"}}
  ]
}}
Rules:
- YOU OWN THE PLAN — you are the ONLY agent that changes the goal tree or the
  queue. If the work that should happen next has no place in the tree, CHANGE
  THE TREE (`goal_changes`: add a branch, park a dead one, set-status, retire,
  reparent). Never squeeze work under a goal it does not serve, and NEVER ask
  the owner for permission to change the plan: the owner reviews it AFTERWARDS
  (each change is shown to them with your `why`, and is one command to undo).
  The long-term VISION is the owner's and cannot be changed here.
  A branch you add may be queued against in the SAME plan: put the branch's id
  (or its exact title) in the queue entry's "advances" — code resolves a title to
  the id. Do not add a branch that already exists: change it instead.
- MAINTENANCE, NOT REGENERATION: every entry already in
  signals.repo.current_queue must be judged this cycle — carried forward,
  superseded by a new entry, or dropped with a reason. Do not rebuild the queue
  from scratch: keep what still serves the plan, and reorder only where the
  active milestone says so. (Carried-forward work must still be valid: an entry
  whose goal no longer exists must be re-pointed at a live one or dropped.)
- HANDOVER: queue[0] is the task THE NEXT RUN BUILDS. Put the single most
  valuable next step for the ACTIVE milestone first — the runner-up second, and
  so on. The order you write IS the execution order.
- GOAL DISCIPLINE (above all else): when a goal tree is given, the work you
  queue must MOVE THE ACTIVE MILESTONE — order the queue so the entries that
  most directly advance it come first. Every queue entry carries "advances"
  with a milestone id from the tree. If a task advances nothing, set
  "advances": "none" AND list its label in "off_goal" with a reason — off-goal
  work is allowed but never silent. Validation rejects unknown ids, missing
  "advances", and entries pointing at a milestone that is already done. The
  entry's "reason" must name the milestone MEASURE it serves, so the mapping is
  auditable in the run log (a task that cannot cite a measure is off-goal).
- MILESTONE VERDICT: judge the active milestone honestly against its measures.
  Metric measures are checked by code too — do not contradict them. Only claim
  met:true when the evidence supports it; if the milestone is met, the next
  entry is to propose the NEXT milestone (as a feature/bugfix that starts it,
  or an issues_to_file for an owner decision) rather than inventing off-goal
  polish.
- Buildability bias: the pipeline must be able to END EVERY RUN IN A BUILD.
  Queue executable work (motion/bugfix/feature) first; investigations are
  capped at 2 entries and each MUST, in its "reason", name the owner decision
  or the code task it feeds — a decision-linked investigation is fine,
  analysis-for-its-own-sake is not.
- An issues_to_file entry counts as the run's OWNER ASK; if you can see no
  buildable work at all, your job is the ask: file the decision issue that
  would unlock one (e.g. a feature proposal for approval, a freeze lift, a
  scope subtraction) — never a "what should we do?" question. Production
  promotion is NOT unlockable work: it is human-only (see the rule below).
- queue ordering = execution order; at most {max_queue} entries.
- IMPACT REVIEW (required): signals.repo.current_queue is the queue already
  waiting to be built. Judge EVERY entry's fate before proposing. If your plan
  makes an entry irrelevant — already covered by new work, same target+type, or
  a better replacement — list its exact label in the relevant new entry's
  "supersedes". If you remove an entry for any other reason, list it in
  top-level "dropped" with a one-line reason. A queued task must never vanish
  without a verdict: code records every verdict and prints the impact block.
  Labels MUST match current_queue exactly (validation rejects unknown labels).
- motion tasks may ONLY target listed remaining surfaces (copy the label exactly).
  If codebase.remaining is empty, queue must contain NO motion entries — any
  motion proposal without a listed surface is rejected by validation.
- signals.ci: a red delivery (workflow failure, e.g. a store upload or policy
  rejection) is the product's fire — restoring delivery outranks everything.
  When signals.ci.last_conclusion is "failure", the FIRST queue entry must be
  a bugfix/feature that restores the delivery (grounded in failure_log_tail;
  name the exact files to change). Validation code enforces this ordering.
- repo.recent_runs is the ledger of what was ALREADY executed/blocked/rejected.
  Do NOT repeat those tasks; build on their findings and note outcomes instead.
  A previously proposed item that recent_runs shows blocked/rejected must not
  reappear unless the underlying cause clearly changed.
- no new domain features unless the research/constraints say the freeze lifted.
- issues_to_file ONLY for decisions needing the owner (e.g. re-enabling
  disabled features, analytics instrumentation sign-off, a freeze lift,
  an owner call on the next milestone when the active one is met).
  PRODUCTION PROMOTION IS NEVER SUCH A DECISION: promoting a built release
  beyond internal testing is human-only and the owner's own manual step in Play
  Console. Never ask about it, never file an issue about it, never queue work
  for it — a release sitting on internal testing is the run's DELIVERABLE and
  may be reported as a fact, never as an open question.
  NEVER re-file or re-raise anything listed in
  constraints.standing_decisions — those are answered policy; the data behind
  them may be reported as facts, not as an open question. Do not re-file an
  issue that recent_runs/notes or the described
  consensus already answers; check before queueing one.


RESPOND WITH JSON ONLY."""


def _extract_json(text: str) -> dict:
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    return json.loads(raw[start:end + 1])


def _validate(result: dict, research: dict | None = None) -> str | None:
    if "queue" not in result or not isinstance(result["queue"], list):
        return "missing list 'queue'"
    remaining: list[dict] = []
    if research is not None:
        remaining = (research.get("signals", {}).get("codebase", {})
                     .get("remaining")) or []
    for q in result["queue"][:MAX_QUEUE]:
        if not q.get("type") or not q.get("label") or not q.get("reason"):
            return "queue entry missing type/label/reason"
        if research is not None:
            ci_last = (research.get("signals", {}).get("ci", {}) or {}).get("last_conclusion")
            if q is result["queue"][0] and ci_last == "failure" \
                    and q.get("type") not in ("bugfix", "feature"):
                return ("signals.ci is red — the first queue entry must be the "
                        "bugfix/feature that restores delivery")
        if q.get("type") == "motion":
            labels = {r.get("label"): set(r.get("paths") or []) for r in remaining}
            if q["label"] not in labels:
                return (f"motion queue entry '{q['label']}' is not a listed "
                        "remaining surface — motion targets may only come from "
                        "codebase.remaining" + (" (it is empty now)" if not remaining else ""))
            tgt = q.get("target") or ""
            if tgt and tgt != q["label"] and tgt not in labels[q["label"]]:
                return (f"motion target '{tgt}' is not one of the surface's "
                        f"listed paths {sorted(labels[q['label']])}")
    if "insights" not in result or not isinstance(result.get("insights"), dict):
        return "missing object 'insights'"
    if not isinstance(result.get("issues_to_file", []), list):
        return "'issues_to_file' must be a list"
    if not isinstance(result.get("dropped", []), list):
        return "'dropped' must be a list"
    # Impact review is judged: every supersede/drop label must name a task that
    # is actually queued (no inventing, no ghost labels).
    if research is not None:
        queued = {queue_mod._norm(queue_mod.label_of(q))
                  for q in (research.get("signals", {}).get("repo", {})
                            .get("current_queue") or [])}
        claims = []
        for q in result["queue"]:
            claims += [(lbl, "supersedes") for lbl in (q.get("supersedes") or [])]
        claims += [(d.get("label"), "dropped") for d in result.get("dropped", [])]
        for lbl, field in claims:
            if not lbl:
                continue
            if not queued:
                return (f"{field} names '{lbl}' but signals.repo.current_queue "
                        "is empty — there is nothing to supersede or drop")
            if queue_mod._norm(lbl) not in queued:
                return (f"{field} label '{lbl}' is not in "
                        "signals.repo.current_queue — impact review may only name "
                        "already-queued tasks, or add new work to the queue")
    if result["queue"]:
        if sum(1 for q in result["queue"] if q.get("type") == "investigation") > 2:
            return "at most 2 investigation entries per cycle"
        executable = any(q.get("type") in ("motion", "bugfix", "feature")
                         for q in result["queue"])
        if not executable and not result.get("issues_to_file"):
            return ("queue has no executable work and no owner-ask issue — "
                    "a run must end in a build, an owner ask, or both")
    if not isinstance(result.get("off_goal", []), list):
        return "'off_goal' must be a list"
    return None


def _research_signals(project: Path) -> dict:
    """The signals collected for THIS cycle (written by research_collect just
    before synthesis) — used for deterministic metric-measure evaluation."""
    f = project / ".pipeline" / "research.json"
    try:
        return json.loads(f.read_text()).get("signals", {}) or {}
    except Exception:
        return {}


def _llm(prompt: str, log: RunLog, timeout: int = 900) -> str:
    from .core import agent_argv, role_cfg
    cfg = role_cfg("product_synthesis")
    argv = agent_argv(cfg, prompt)
    if not argv:
        raise SystemExit(
            "synthesis needs an agentic-tool command — set "
            "roles.product_synthesis.command in config/runtimes.json or export "
            "DEVTEAM_AGENT_CMD (e.g. '<your-tool> run --model {model} {prompt}'). "
            "No vendor is baked into the team.")
    log(f"~ synthesis via {argv[0]} (model {cfg.get('model', 'default')})")
    r = run(argv, None, timeout=timeout)
    if r.returncode != 0:
        raise SystemExit("synthesis LLM failed: " +
                         ((r.stdout or "") + (r.stderr or ""))[-400:])
    return r.stdout


def research_apply(project: Path, result: dict, log: RunLog) -> None:
    state = state_of(project)
    new_queue = result.get("queue", [])[:MAX_QUEUE]
    # Not a silent overwrite: judge what the new plan makes irrelevant, record
    # every verdict, and print the impact block for the run banner.
    block = queue_mod.apply_plan(state, new_queue, source="research",
                                 explicit_drops=result.get("dropped", []))
    state["insights"] = result.get("insights", {})
    state["last_research_at"] = now_iso()
    # Milestone verdicts: code evaluates metric measures (deterministic); the
    # synthesis's verdict is only recorded for measures code cannot check, and
    # code's FAIL always wins over an LLM "met". Retiring a milestone advances
    # the active pointer deterministically (goals.status_of).
    profile = profile_of(project)
    act = goals.active_milestone(profile, state)
    verdict = result.get("milestone_verdict") or {}
    if act:
        code_ok, evidence = goals.check_measures(act, _research_signals(project))
        if code_ok is False:
            goals.record_verdict(state, act["id"], "active",
                                 f"metric measure failed: {evidence}", "code")
            log(f"goal: [{act['id']}] not met — {evidence}")
        elif code_ok is True and verdict.get("id", act["id"]) == act["id"]:
            goals.record_verdict(state, act["id"], "done",
                                 f"all metric measures pass: {evidence}", "code")
            log(f"goal: [{act['id']}] MET by code — {evidence}; advancing to the "
                "next milestone")
        elif verdict.get("id") == act["id"]:
            goals.record_verdict(state, act["id"],
                                 "done" if verdict.get("met") else "active",
                                 str(verdict.get("evidence", "")), "research")
            log(f"goal: [{act['id']}] verdict "
                f"{'MET' if verdict.get('met') else 'not met'} (research) — "
                f"{str(verdict.get('evidence',''))[:120]}")
        if verdict.get("id") and verdict.get("id") != act["id"]:
            log(f"goal: ignored verdict for '{verdict['id']}' — the active "
                f"milestone is '{act['id']}'")
    elif verdict.get("id") and goals.milestone(profile, str(verdict["id"])):
        log(f"goal: verdict for '{verdict['id']}' ignored — every milestone is "
            "already done; the next one must come from the owner")
    for q in state.get("queue", []):
        mid = goals.queue_entry_milestone(q, profile, state)
        node = goals.find_node(profile, mid) if mid else None
        q["advances"] = mid or q.get("advances") or ""
        if node:
            log(f"  goal: '{q['label'][:40]}' → [{node['id']}] {node['title'][:60]}")
    for og in (result.get("off_goal") or []):
        log(f"  goal: OFF-GOAL '{str(og.get('label',''))[:40]}' — "
            f"{str(og.get('reason',''))[:80]}")
    from pathlib import Path as _P
    research_file = _P(project) / ".pipeline" / "research.json"
    try:
        state["upgrades"] = json.loads(research_file.read_text()).get("upgrades", [])
    except Exception:
        pass
    save_state(project, state)
    for line in block.splitlines():
        log(line)
    for issue in result.get("issues_to_file", []):
        title, body = issue.get("title", ""), issue.get("body", "")
        if not title:
            continue
        from . import alarm
        ok, out = alarm.file_issue(project and profile_of(project)["repo_slug"],
                                   title[:160], body[:5000])
        log(f"issue {'filed' if ok else 'FAILED'}: {title[:60]}  {out[:120]}")
    from . import scribe
    scribe.update(project)
    log(f"research: applied — queue={len(state['queue'])}, "
        f"head='{(state['queue'][0]['label'] if state['queue'] else '—')}'")


# --------------------------------------------------------------------------- #
# entry
# --------------------------------------------------------------------------- #

def capability_upgrades(signals: dict, profile: dict, project: Path | None = None) -> list[dict]:
    """Deterministic advisory tier: what the OWNER could connect to make the
    pipeline smarter. Presence-driven — a satisfied upgrade drops out
    automatically. Never hardcoded to a project; collectors may grow."""
    up: list[dict] = []
    ga4 = signals.get("ga4", {})
    ga4c = (profile.get("connections") or {}).get("ga4") or {}
    if ga4.get("status") != "ok" or ga4.get("new_users_7d") is None:
        up.append({
            "id": "analytics",
            "title": "Connect app analytics (GA4 / Firebase)",
            "benefit": "uninstall rate, new-user trend and churn events turn "
                       "priority calls from vibes to numbers",
            "how": "declare profile.connections.ga4 {property: <GA4 property id>} "
                   "— auth is the shared owner login (gcloud auth application-default "
                   "login --scopes=https://www.googleapis.com/auth/analytics.readonly, "
                   "one time, all projects); `devteam doctor` verifies",
        })
    elif project is not None:
        # connected but reading flat zero — first suspect is no instrumentation,
        # i.e. the app ships no Firebase Analytics SDK at all
        import re as _re
        gradle_text = ""
        for cand in ("composeApp/build.gradle.kts", "app/build.gradle",
                     "app/build.gradle.kts", "gradle/libs.versions.toml"):
            f = project / cand
            if f.exists():
                gradle_text += f.read_text(errors="ignore")
        instrumented = bool(_re.search(
            r"firebase-analytics|FirebaseAnalytics|play.services.analytics|analytics-ktx",
            gradle_text))
        flat = not any((ga4.get(k) or 0) for k in ("new_users_7d", "active_users_7d"))
        if not instrumented and flat:
            up.append({
                "id": "analytics-instrumentation",
                "title": "Instrument the app with the Firebase Analytics SDK",
                "benefit": "the GA4 connection is live but the app emits no events "
                           "(no SDK) — numbers stay zeros; priorities stay vibes",
                "how": "owner: create/link the app in the Firebase console and "
                       "download google-services.json; dev-team feature task wires "
                       "firebase-analytics into androidMain (owner approves in next intake)",
            })
    if not profile.get("connections", {}).get("play_reviews"):
        up.append({
            "id": "store-reviews",
            "title": "Connect store reviews",
            "benefit": "public churn reasons from users who never open the app "
                       "again — the only channel that reaches install→uninstall same-day users",
            "how": "phase while the app is unpublished: this lands only after a "
                   "Play/App presence exists; recheck `devteam research` after ship",
        })
    if not profile.get("connections", {}).get("play_vitals"):
        up.append({
            "id": "vitals",
            "title": "Connect Play vitals (crash/ANR per version)",
            "benefit": "releases with regressions get flagged before users report",
            "how": "Play Developer API service account (same JSON as the "
                   "internal-testing upload) — declared under connections.play_vitals",
        })
    return up


def render_prompt(project: Path, research: dict, log: RunLog | None = None) -> str:
    """The exact synthesis prompt (goals + research + rules). Exported by
    --no-llm so a host tool that has no subprocess command can hand it to its
    OWN native agent verbatim, then apply the returned JSON with
    `research --apply-result <file>` — same validation, same goal gate."""
    profile, state = profile_of(project), state_of(project)
    if log and goals.has_goals(profile):
        act = goals.active_milestone(profile, state)
        log(f"goal: active milestone = {act['id'] if act else 'none'} "
            f"({(profile.get('goals') or {}).get('vision','')[:80]})")
    return PROMPT_TMPL.format(project=research["project"],
                              goals=goals.prompt_block(profile, state),
                              research=json.dumps(research, indent=2),
                              max_queue=MAX_QUEUE)


def _preview_goal_changes(profile: dict, result: dict) -> tuple[dict, list, str]:
    """Apply the plan's tree changes to a COPY, so the whole plan is validated
    against the tree it would actually have — a plan may add a branch and queue
    work against it in the same cycle (validate-before-apply used to reject
    exactly that). Returns (trial_profile, branches_added, error)."""
    import copy
    trial = copy.deepcopy(profile)
    added: list = []
    for c in result.get("goal_changes") or []:
        nid, _what, _why, err = goals.apply_one(trial, c, by="agent")
        if err:
            return trial, added, f"goal_changes rejected: {err}"
        if str(c.get("action") or "") == "add":
            added.append({"id": nid, "title": str(c.get("title") or "")})
    return trial, added, ""


def _alias_advances(result: dict, added: list) -> None:
    """A plan may add a branch and queue work for it in one cycle. An agent cannot
    always know the id code generates from a title, so a queue entry that names an
    added branch by its TITLE (or the title slugged) is resolved here — the same
    deterministic repair the rest of the pipeline does for references. Anything
    still unknown is left alone for validation to reject."""
    def slug(t: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", (t or "").lower()).strip("-")
    alias: dict[str, str] = {}
    for a in added:
        alias[str(a["title"]).strip().lower()] = a["id"]
        alias[slug(str(a["title"]))] = a["id"]
    for q in result.get("queue") or []:
        v = str(q.get("advances") or "").strip()
        if v and v.lower() in alias:
            q["advances"] = alias[v.lower()]


def validate_plan(project: Path, result: dict, research: dict | None) -> str | None:
    """The whole deterministic gate on a planner result: output schema, tree-change
    schema, the tree changes themselves (on a copy), reference repair, then the
    goal gate on the queue — with the would-be branches visible, so adding a
    branch and queueing for it in one plan is legal. Nothing is trusted because an
    agent said it."""
    profile, state = profile_of(project), state_of(project)
    err = _validate(result, research)
    if not err:
        err = goals.validate_changes(result)
    trial = profile
    if not err:
        trial, added, err = _preview_goal_changes(profile, result)
        if not err:
            _alias_advances(result, added)
    if not err:
        err = goals.validate_advances(
            result, trial, state,
            extra_ids=tuple(goals.change_ids(result, profile)))
    return err


def apply_goal_changes(profile: dict, state: dict, result: dict) -> list[dict]:
    """Apply the plan's tree changes for real — the SAME engine as `devteam goal`
    (goals.apply_one) and recorded the same way: the whole goals block before and
    after each change, a mandatory plain-words reason, and the owner reviews
    afterwards (never a permission gate, and exactly revertible)."""
    import copy
    applied: list[dict] = []
    for c in result.get("goal_changes") or []:
        before = copy.deepcopy(profile.get("goals") or {})
        node_id, what, why, err = goals.apply_one(profile, c, by="agent")
        if err:
            raise SystemExit(f"goal change rejected: {err}")
        if what.startswith("already existed"):
            # Re-reading the same plan: nothing moved, so nothing is recorded —
            # no misleading entry in the owner's change log, no fake "plan moved".
            continue
        after = copy.deepcopy(profile.get("goals") or {})
        e = goals.change(state, what, node_id, why or str(c.get("why") or ""),
                         by="agent")
        e["goals_before"], e["goals_after"] = before, after
        applied.append({"node": node_id, "what": what, "why": e["why"]})
    return applied


def apply_result(project: Path, result: dict, research: dict | None,
                 log: RunLog) -> dict:
    """Validate, then apply a planner result: the TREE first (so the queue can
    point at branches this same plan adds), then the queue — and stamp the
    review so `status`/`pick` can see the plan was reviewed for this run."""
    err = validate_plan(project, result, research)
    if err:
        log(f"planner: result REJECTED — {err}")
        raise SystemExit("planner result rejected: " + err)

    profile, state = profile_of(project), state_of(project)
    changes = apply_goal_changes(profile, state, result)
    if changes:
        save_json(pipeline_dir(project) / "profile.json", profile)
        # The audit entry MUST hit the disk with the change it describes: the
        # owner's review banner and `goal revert` read it, and a later state
        # reload (research_apply does one) would otherwise drop it.
        save_state(project, state)
        log(f"planner: the plan moved ({len(changes)} change"
            f"{'s' if len(changes) != 1 else ''}): "
            + "; ".join(f"{c['what']} '{c['node']}'" for c in changes))
    research_apply(project, result, log)
    st = state_of(project)
    head = (st.get("queue") or [{}])[0].get("label", "") if st.get("queue") else ""
    st["plan_review"] = {"at": now_iso(), "head": head,
                         "queue": len(st.get("queue") or []),
                         "tree_changes": len(changes),
                         "changes_count": len(changes),
                         "changes": changes,
                         "by": "planner"}
    save_state(project, st)
    log(f"planner: reviewed — {len(st.get('queue') or [])} waiting, next run "
        f"builds '{head or '—'}'")
    return {"head": head, "changes": changes, "queue": st.get("queue") or []}


def reviewed_today(state: dict) -> bool:
    """Was the plan reviewed by the planner today? The workflow's step 2 does it
    on every run; this is how code can SEE that it happened (never a hard gate —
    a run with no LLM available must still be able to ship)."""
    at = str(((state.get("plan_review") or {}).get("at")) or "")
    return bool(at) and at[:10] == now_iso()[:10]


def plan_review_line(project: Path) -> str:
    """One plain line for the run banner/status: when the plan was last reviewed,
    what it left waiting, and which task that review handed this run. A review
    that has not happened today says so out loud (step 2 is the fix)."""
    state = state_of(project)
    lv = state.get("plan_review") or {}
    if not lv:
        return ("plan review: none yet — the run's step 2 reviews the plan "
                f"(`devteam plan {project.name}`)")
    when = str(lv.get("at") or "")[:16].replace("T", " ")
    stale = "" if reviewed_today(state) else \
        "  ⚠ NOT reviewed today — step 2 of this run does it"
    head = str(lv.get("head") or "") or "nothing — this run is an owner ask"
    return (f"plan review: {when} — {lv.get('queue', 0)} waiting, builds "
            f'"{head}"{stale}')


def plan_report(project: Path) -> str:
    """The owner-facing review block: where we are heading in plain words, what
    is left, what the planner changed and why, and — the handover — the ONE task
    this run builds. No ids, no paths, no commands."""
    from . import tree as tree_mod
    profile, state = profile_of(project), state_of(project)
    goals_block = goals.plan(profile) or {}
    vision = str(goals_block.get("vision") or "").strip()
    act = goals.active_milestone(profile, state) if goals_block else None
    queue = state.get("queue") or []
    lv = state.get("plan_review") or {}
    head = str(lv.get("head") or (queue[0].get("label") if queue else ""))
    L = ["PLAN REVIEW — where this is going, and what the run does next"]
    if vision:
        L.append(f"  heading for:      {vision}")
    else:
        L.append("  heading for:      no long-term goal yet — the team has asked "
                 "you for one sentence (OWNER ASK below)")
    if act:
        L.append(f"  working toward:   {act.get('title', '')}")
    elif goals_block:
        L.append("  working toward:   every step is done — the next one is yours "
                 "to name")
    if not queue:
        L.append("  left to do:       nothing executable — this run is an ask to "
                 "you, not a build")
    else:
        L.append(f"  left to do:       {len(queue)} waiting")
    L.append(f"  THIS RUN BUILDS:  {head or '— (nothing queued)'}")
    if queue:
        L.append("  the queue, in order:")
        for i, q in enumerate(queue, 1):
            label = tree_mod._humanize(str(q.get("label") or "?"))
            mid = goals.queue_entry_milestone(q, profile, state)
            # any branch counts, not just a milestone: work may serve a sub-goal
            node = goals.find_node(profile, mid) if mid else None
            serv = f"   serves \"{node['title']}\"" if node \
                else "   (no goal declared)"
            mark = "  ← NEXT" if i == 1 else ""
            L.append(f"    {i}. {label}{mark}{serv}")
    # Only what THIS review changed counts as "the plan moved" — a change made by
    # an earlier cycle today must not be re-reported every run.
    n_made = int(lv.get("changes_count") or 0)
    recent = [e for e in (state.get("tree_changes") or [])
              if not e.get("reverted")][-n_made:] if n_made else []
    if recent:
        L.append("  the plan moved (you can undo any of it — `devteam goal "
                 f"{project.name} log` shows all, `revert` undoes the last):")
        for e in recent[-4:]:
            L.append(f"    · {e.get('action')} '{e.get('node')}' — {e.get('why')}")
    else:
        L.append("  the plan:         unchanged this cycle — the queue still "
                 "serves the same step")
    return "\n".join(L)


def research_apply_file(project: Path, result_path: Path, log: RunLog) -> None:
    """Apply a synthesis result produced elsewhere (the host tool's native
    agent). Identical gates to the in-process LLM path: schema, impact review,
    and the goal gate. Nothing is trusted just because it came from an agent."""
    try:
        result = _extract_json(result_path.read_text())
    except Exception as e:
        raise SystemExit(f"cannot read synthesis result {result_path}: {e}")
    try:
        research = json.loads((project / ".pipeline" / "research.json").read_text())
    except Exception:
        research = None
    log(f"planner: apply-result received ({len(result.get('queue', []))} entries, "
        f"{len(result.get('goal_changes') or [])} plan changes) — validating")
    apply_result(project, result, research, log)


def research_run(project: Path, log: RunLog, use_llm: bool = True) -> bool:
    """Collect (always), then synthesize + apply (unless --no-llm). Returns True
    when a plan was actually applied, False when the prompt was only written out
    for the host tool's own agent."""
    research = research_collect(project, log)
    if not use_llm:
        # Hand the prompt to the calling agent: the team's contract is "the LLM
        # proposes, code validates and applies", and the host tool may be the
        # only thing that can spawn an agent (no roles.*.command configured).
        p = project / ".pipeline" / "research-prompt.txt"
        p.write_text(render_prompt(project, research, log))
        log(f"research: --no-llm stop; prompt → {p.name} "
            "(apply the agent's JSON with `research <project> --apply-result <file>`)")
        return False
    prompt = render_prompt(project, research, log)
    result, err = None, ""
    for attempt in (1, 2):
        raw = _llm(prompt, log)
        try:
            result = _extract_json(raw)
            err = validate_plan(project, result, research) or ""
            if not err:
                break
        except json.JSONDecodeError as e:
            err = f"invalid JSON: {e}"
        log(f"  synthesis attempt {attempt} rejected: {err[:160]}")
        prompt += "\n\nYOUR PREVIOUS OUTPUT WAS REJECTED: " + err + \
                  "\nReturn STRICT JSON matching the schema, nothing else."
    if not result or err:
        log("research: synthesis failed twice — raw research kept; "
            "orchestrator may decide manually from .pipeline/research.json")
        raise SystemExit(2)
    apply_result(project, result, research, log)
    return True
