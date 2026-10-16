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
from .core import RunLog, now_iso, profile_of, run, save_state, state_of

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
        # per-project service account (the MindfulLauncher pattern)
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
                "queue_history": state.get("queue", []),
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

You are given research collected by deterministic code:
{research}

Decide what to BUILD NEXT. Output STRICT JSON only (no prose, no code fence):
{{
  "insights": {{"headline": "one line", "analysis": "2-5 sentences grounded in the numbers"}},
  "queue": [
    {{"type": "motion|bugfix|investigation", "label": "...",
      "target": "<exact surface path for motion; short topic otherwise>",
      "priority": 1, "reason": "one line grounded in research"}}
  ],
  "issues_to_file": [
    {{"title": "...", "body": "what + why + recommended action + effort/risk"}}
  ]
}}
Rules:
- Buildability bias: the pipeline must be able to END EVERY RUN IN A BUILD.
  Queue executable work (motion/bugfix/feature) first; investigations are
  capped at 2 entries and each MUST, in its "reason", name the owner decision
  or the code task it feeds — a decision-linked investigation is fine,
  analysis-for-its-own-sake is not.
- An issues_to_file entry counts as the run's OWNER ASK; if you can see no
  buildable work at all, your job is the ask: file the decision issue that
  would unlock one (e.g. a feature proposal for approval, a freeze lift, a
  promotion) — never a "what should we do?" question.
- queue ordering = execution order; at most {max_queue} entries.
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
- issues_to_file ONLY for decisions needing the owner (e.g. production
  promotion, re-enabling disabled features, analytics instrumentation
  sign-off). NEVER re-file or re-raise anything listed in
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
    if result["queue"]:
        if sum(1 for q in result["queue"] if q.get("type") == "investigation") > 2:
            return "at most 2 investigation entries per cycle"
        executable = any(q.get("type") in ("motion", "bugfix", "feature")
                         for q in result["queue"])
        if not executable and not result.get("issues_to_file"):
            return ("queue has no executable work and no owner-ask issue — "
                    "a run must end in a build, an owner ask, or both")
    return None


def _llm(prompt: str, log: RunLog, timeout: int = 900) -> str:
    from .core import runtimes
    role = runtimes()["roles"]["product_synthesis"]
    model = role.get("model", "")
    primary = run(["opencode", "run", "--auto", "--model", model, prompt],
                  None, timeout=timeout)
    if primary.returncode == 0:
        return primary.stdout
    fold = ((primary.stdout or "") + (primary.stderr or ""))[-400:]
    fallback = run(["claude", "-p", prompt, "--output-format", "text"],
                   None, timeout=timeout)
    if fallback.returncode != 0:
        raise SystemExit("synthesis LLM failed: " + fold +
                         " / " + (fallback.stdout + fallback.stderr)[-400:])
    return fallback.stdout


def research_apply(project: Path, result: dict, log: RunLog) -> None:
    state = state_of(project)
    state["queue"] = result.get("queue", [])[:MAX_QUEUE]
    state["insights"] = result.get("insights", {})
    state["last_research_at"] = now_iso()
    from pathlib import Path as _P
    research_file = _P(project) / ".pipeline" / "research.json"
    try:
        state["upgrades"] = json.loads(research_file.read_text()).get("upgrades", [])
    except Exception:
        pass
    save_state(project, state)
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


def research_run(project: Path, log: RunLog, use_llm: bool = True) -> None:
    research = research_collect(project, log)
    if not use_llm:
        log("research: --no-llm stop; agent reads .pipeline/research.json directly")
        return
    prompt = PROMPT_TMPL.format(project=research["project"],
                                research=json.dumps(research, indent=2),
                                max_queue=MAX_QUEUE)
    log("~ synthesis: one LLM call (strict JSON, code-validated)")
    result, err = None, ""
    for attempt in (1, 2):
        raw = _llm(prompt, log)
        try:
            result = _extract_json(raw)
            err = _validate(result, research)
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
    research_apply(project, result, log)
