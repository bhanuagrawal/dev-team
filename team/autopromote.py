"""A recorded owner ask, executed after CI: promotion is still a verb.

WHY THIS EXISTS (owner directive 2026-11-17)
--------------------------------------------
"promote to production after ci complete always — if release candidate — create
a provision for doing this for an app when user ask in dev team"

THE WEEKLY FRESHNESS RULE (owner directive 2026-11-24)
------------------------------------------------------
"on every workflow run, check since when we promote a release candidate to prod,
if its been more than 7 days, also promote to prod"

That is a second, team-wide standing ask, recorded in `config/promote.json` and
executed by the same `--run` the nightly already calls: when production has not
changed in more than `max_days` (7) days, the newest release candidate goes out.
The age is read LIVE from Google Play on every check — the production track plus
the store listing's own "Updated on" line — and NEVER from `state.json`, so a
promotion this disk remembers cannot make a stale app look fresh. An app with no
per-app provision is still eligible: this rule is the owner's global answer, not
an inference. An explicit `rc --no` for the exact version still vetoes it, and a
date Play cannot verify holds the rule instead of guessing.

THE CRITICAL-FIX EARLY LAUNCH (owner directive 2026-11-25)
----------------------------------------------------------
"…it needs light modification that we need to intelligently determine whether
a build should go when the one week has not passed, in case it is a crucial
fix…" — answered with an explicit marker, not an inference from commit text:
`devteam rc <app> --yes --critical --note "<why>"` marks the newest release
candidate as a crucial fix, and that marker alone shortens the wait. The
marker's author, words and date are carried into `requested_by` so the launch
still names a sentence somebody wrote. Nothing else moves: `rc --no` still
vetoes, the build must be on internal, Play still has to answer (an unknown
age holds), and every launch still goes through `promote()`.

Until today `promote` was reachable only by naming the version in the same
conversation: right for a one-off launch, wrong for an app whose owner has
decided that every build the pipeline calls a release candidate, once CI has put
it on the internal track, should go out. That decision is now recorded ONCE, as
a per-project provision (`profile["promote"]["after_ci"]`), and the team
executes it afterwards instead of asking again every time.

WHAT THE PROVISION MEANS, AND WHAT STILL HAS TO BE TRUE
-------------------------------------------------------
  * `--on` refuses without the owner's own words, and stores them with a
    timestamp; `--off` keeps the history instead of erasing it. There is no
    other way to arm this: no default, no inheritance between apps.
  * CI gains NOTHING. The canonical workflow still uploads to `internal` only
    (`team/ci.py`), and this module runs from the team — the nightly, or
    `devteam autopromote <app> --run --yes` by hand.
  * "after CI completes" is not a timer and not a green log. It is Play's
    internal track reading back the build with a completed release. A build CI
    never delivered is not promotable, and the verb says that instead of
    guessing.
  * An explicit `rc --no` call for the exact version is a VETO — the owner's
    "if release candidate" clause, read from the same ledger the handover and
    `status` use. A YES or a missing call lets the provision run: the provision
    itself is the owner's standing answer.
  * The version promoted is always the NEWEST shipped one. A superseded build
    is not a launch, so older un-promoted builds are skipped rather than queued.
  * Every commit still goes through `promote()`: note gate, full rollout, edit
    id, fresh read-back, and a `requested_by` that carries the standing words —
    so "who asked for this launch" is still a sentence the owner wrote, not an
    inference from a boolean.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .core import (TEAM_ROOT, profile_of, profile_root, save_json, save_state,
                   state_of)
from .promote import production_now, promote

LOG_KEY = "promote_provision_runs"
HISTORY_KEEP = 20
WEEKLY_RULE_PATH = TEAM_ROOT / "config" / "promote.json"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def provision_of(project: Path) -> dict:
    """The app's promotion provision, exactly as written by the owner's ask."""
    return dict(profile_of(Path(project)).get("promote") or {})


def enabled(project: Path) -> bool:
    return bool(provision_of(Path(project)).get("after_ci"))


def provision(project: Path, *, on: bool, requested_by: str = "",
              note: str = "") -> dict:
    """Arm or disarm the standing promotion for one app.

    `--on` is an owner act: without their words there is nothing to store, and a
    provision nobody can attribute is worse than no provision. `--off` needs no
    words (stopping must always be available), but it is recorded with the same
    timestamp so the history shows who ended it and when.
    """
    project = Path(project)
    words = str(requested_by or "").strip()
    if on and not words:
        raise SystemExit(
            "autopromote --on needs --requested-by \"<the owner's words>\" — a standing "
            "production promotion is recorded as a sentence the owner wrote, never as "
            "an agent's inference")

    profile = profile_of(project)
    block = dict(profile.get("promote") or {})
    history = [h for h in (block.get("history") or []) if isinstance(h, dict)]
    entry = {"at": _now(), "after_ci": bool(on), "requested_by": words, "note": note}
    history.append(entry)
    block["after_ci"] = bool(on)
    block["history"] = history[-HISTORY_KEEP:]
    if on:
        block["requested_by"] = words
        block["at"] = entry["at"]
    else:
        block["off_at"] = entry["at"]
        if words:
            block["off_by"] = words
    profile["promote"] = block
    # The profile is project state written by the team, never hand-edited
    # (`devteam firebase` / `runner` / `adapt` hold the same rule). `profile_root`
    # resolves the project that OWNS the profile, so arming from a worktree never
    # leaves a second copy behind.
    save_json(profile_root(project) / ".pipeline/profile.json", profile)
    return block


def _versions(state: dict) -> list[str]:
    """Shipped versions, newest first, de-duplicated.

    The shipped ledger is authoritative (the checkout may lag origin/main), with
    `last_run.version` as the fallback a fresh delivery writes before `record`.
    """
    out: list[str] = []
    for row in reversed(state.get("shipped") or []):
        v = str(row.get("version") or "").strip()
        if v and v != "?" and v not in out:
            out.append(v)
    if not out:
        last = str((state.get("last_run") or {}).get("version") or "").strip()
        if last and last != "?":
            out.append(last)
    return out


def candidate(state: dict) -> str:
    """The one version the provision would launch: the newest shipped build."""
    versions = _versions(state)
    return versions[0] if versions else ""


def rc_call(state: dict, version: str) -> dict | None:
    """The newest release-candidate call for `version` (a later call wins)."""
    for call in reversed(state.get("release_candidates") or []):
        if str(call.get("version") or "").lstrip("v") == str(version).lstrip("v"):
            return call
    return None


def already_promoted(state: dict, version: str) -> bool:
    want = str(version).lstrip("v")
    return any(str(p.get("version") or "").lstrip("v") == want
               for p in state.get("promotions") or [])


def weekly_rule() -> dict:
    """The owner's team-wide weekly freshness rule, exactly as recorded."""
    try:
        block = json.loads(WEEKLY_RULE_PATH.read_text())
    except Exception:
        return {}
    return block if isinstance(block, dict) else {}


def critical_rule() -> dict:
    """The owner's 2026-11-25 early-launch rule for an explicitly critical RC."""
    block = weekly_rule().get("critical_early")
    return dict(block) if isinstance(block, dict) else {}


def _code_of(state: dict, version: str) -> str:
    """The versionCode the shipped ledger gives a version ('' when unknown)."""
    want = str(version).lstrip("v")
    for row in reversed(state.get("shipped") or []):
        if str(row.get("version") or "").lstrip("v") == want and row.get("version_code"):
            return str(row["version_code"])
    return ""


def _on_production(state: dict, target: str, live: dict) -> bool:
    """Is the candidate already the live build, by version name or versionCode?"""
    code = _code_of(state, target)
    return (str(live.get("version") or "").lstrip("v") == str(target).lstrip("v")
            or bool(code and code in [str(c) for c in (live.get("versionCodes") or [])]))


def weekly_due(project: Path, *, target: str = "", state: dict | None = None) -> dict:
    """Does the owner's weekly rule fire? Decided by LIVE Play reads only.

    `production_now()` asks Google Play on every call (production track + the
    listing's own "Updated on" date); `state.json` is not consulted for age and
    not consulted for "already live", so a cached promotion record cannot make a
    stale app look fresh or a fresh one look stale.

    Returns `{"due": bool, "why": ..., "production": {...}}`, plus
    `days` and `requested_by` when due. Unknown age FAILS CLOSED — an
    unverifiable production cannot authorize a launch.
    """
    rule = weekly_rule()
    if not rule.get("max_days"):
        return {"due": False, "why": "no weekly promotion rule declared"}
    max_days = int(rule["max_days"])
    project = Path(project)
    state = state if state is not None else state_of(project)
    target = str(target or "").strip() or candidate(state)
    try:
        live = production_now(project)
    except Exception as e:          # a network hiccup must read as "unknown", not a crash
        live = {"version": "", "versionCodes": [], "date": "", "days": None,
                "why": str(e)}
    if not live.get("date"):
        return {"due": False, "production": live,
                "why": ("Play did not say when production last changed"
                        + (f" ({live.get('why')})" if live.get("why") else "")
                        + " — the weekly rule holds rather than guess")}
    if not target:
        return {"due": False, "production": live,
                "why": "the ledger names no shipped version yet"}
    if _on_production(state, target, live):
        return {"due": False, "production": live,
                "why": f"v{target} is already the live production build"}
    days = int(live.get("days") or 0)
    if days <= max_days:
        return {"due": False, "production": live,
                "why": (f"production last changed {days} day(s) ago "
                        f"({live.get('date')}) — the weekly rule fires after {max_days}")}
    words = str(rule.get("requested_by") or "").strip()
    return {
        "due": True,
        "days": days,
        "production": live,
        "why": (f"production has not changed since {live.get('date')} "
                f"({days} days) — the owner's weekly rule promotes v{target}"),
        "requested_by": (f"{words} — standing weekly rule (production unchanged "
                         f"since {live.get('date')}, {days} days)"),
    }


def critical_due(project: Path, *, target: str = "", state: dict | None = None) -> dict:
    """Does the owner's critical-fix rule allow a launch inside the 7-day wait?

    Only an explicit `rc --yes --critical` marker on the NEWEST call for
    `target` can open this door — no keyword scoring of commits or task text.
    A later `rc` call without `--critical` clears the marker (a later call
    wins), and an `rc --no` is still a veto that `run()` respects.

    The age still comes from a LIVE Play read, for two reasons: an already-live
    build must not be launched twice, and an unverifiable age holds rather
    than guesses — the same fail-closed rule the weekly path uses.
    """
    rule = critical_rule()
    if not rule.get("enabled"):
        return {"due": False, "why": "no critical-fix early-launch rule declared"}
    project = Path(project)
    state = state if state is not None else state_of(project)
    target = str(target or "").strip() or candidate(state)
    if not target:
        return {"due": False, "why": "the ledger names no shipped version yet"}
    call = rc_call(state, target)
    if not call or not bool(call.get("rc")) or not bool(call.get("critical")):
        return {"due": False, "why": f"v{target} is not marked a critical fix"}
    try:
        live = production_now(project)
    except Exception as e:
        live = {"version": "", "versionCodes": [], "date": "", "days": None,
                "why": str(e)}
    if not live.get("date"):
        return {"due": False, "production": live,
                "why": ("Play did not say when production last changed"
                        + (f" ({live.get('why')})" if live.get("why") else "")
                        + " — the critical-fix rule holds rather than guess")}
    if _on_production(state, target, live):
        return {"due": False, "production": live,
                "why": f"v{target} is already the live production build"}
    days = int(live.get("days") or 0)
    max_days = int(weekly_rule().get("max_days") or 0)
    if days > max_days:
        # Nothing to decide here: the weekly rule already authorizes this one.
        return {"due": False, "production": live,
                "why": (f"production last changed {days} day(s) ago "
                        f"({live.get('date')}) — the weekly rule already covers it")}
    by = str(call.get("by") or "the agent that made it").strip()
    why = str(call.get("why") or call.get("note") or "").strip()
    marked = f"v{target} marked critical by {by}"
    if why:
        marked += f": {why}"
    at = str(call.get("date") or "").strip()
    if at:
        marked += f" (marked {at})"
    words = str(rule.get("requested_by") or "").strip()
    return {
        "due": True,
        "days": days,
        "production": live,
        "critical": {"version": target, "by": by, "why": why, "at": at},
        "why": (f"{marked} — production last changed {live.get('date')} "
                f"({days} days, inside the {max_days}-day wait); the owner's "
                f"critical-fix rule promotes v{target}"),
        "requested_by": (f"{words} — standing critical-fix rule ({marked}; "
                         f"production last changed {live.get('date')}, {days} days)"),
    }


def _record(project: Path, state: dict, result: dict) -> None:
    """Keep an audit row for every attempt the provision makes by itself.

    The promotion record inside `promote()` answers "what was sent"; this answers
    "what did the standing provision decide, and why" — including the attempts
    that did nothing because CI had not delivered or an `rc --no` vetoed it.
    """
    rows = [r for r in (state.get(LOG_KEY) or []) if isinstance(r, dict)]
    rows.append({
        "at": _now(),
        "version": result.get("version") or "",
        "status": result.get("status") or "",
        "error": result.get("error") or "",
        "rule": result.get("rule") or "after-ci",
        "critical_why": (result.get("critical") or {}).get("why", ""),
        "provisioned_by": (provision_of(project).get("requested_by") or ""),
    })
    state[LOG_KEY] = rows[-50:]
    save_state(project, state)


def run(project: Path, *, version: str = "", yes: bool = False,
        log=None) -> dict:
    """Execute the provision for one app.

    Three recorded asks are executed here: the per-app `after_ci` provision, the
    team-wide weekly rule (`config/promote.json`) that fires when production has
    not changed in more than seven days, and the critical-fix rule that lets an
    explicitly marked RC (`rc --yes --critical`) go out inside that week — both
    with the age read LIVE from Play, never from `state.json`.

    Benign no-ops are statuses, not failures: an unarmed app with fresh
    production, an app whose build is already live, and an app whose newest
    build has not reached `internal` yet all return normally so the nightly
    keeps going. A real refusal (an `rc --no` veto, a missing note, a Play
    error) is reported with the reason in the same dict.
    """
    project = Path(project)
    say = log or (lambda *_: None)
    prov = provision_of(project)
    state = state_of(project)
    target = str(version or "").strip() or candidate(state)
    if not target:
        return {"status": "nothing-to-promote",
                "error": "the ledger names no shipped version yet"}

    # Two standing team-wide asks are executed here, and both apply whether or
    # not this app was armed per app: the 7-day freshness rule, and the critical
    # fix an owner/agent explicitly marked with `rc --yes --critical`. Both read
    # the age from Play live, never from `state.json` — see `weekly_due` and
    # `critical_due`.
    weekly = weekly_due(project, target=target, state=state)
    critical = critical_due(project, target=target, state=state)
    due = weekly if weekly.get("due") else (critical if critical.get("due") else None)
    if not prov.get("after_ci") and not due:
        return {"status": "not-provisioned",
                "error": ("this app has no owner provision for promotion after CI — "
                          f"`devteam autopromote {project.name} --on "
                          "--requested-by \"<the owner's words>\"` arms it; "
                          f"weekly rule: {weekly.get('why') or 'not due'}; "
                          f"critical fix: {critical.get('why') or 'not marked'}")}

    call = rc_call(state, target)
    if call is not None and not bool(call.get("rc")):
        result = {
            "status": "held",
            "version": target,
            "error": (f"v{target} was called NOT a release candidate by "
                      f"{call.get('by') or 'the agent that made it'}"
                      + (f" — {call.get('why') or call.get('note')}" if (call.get('why') or call.get('note')) else "")
                      + f"; the provision does not override that call. Record "
                        f"`devteam rc {project.name} --version {target} --yes --note \"…\"` "
                        "to clear it"),
        }
        _record(project, state, result)
        return result

    if due:
        # A team-wide ask decides — including for an app nobody armed. The record
        # still names the owner's sentence (and, for a critical fix, the marker's
        # author and why), never just a boolean.
        verb = "promotes" if yes else "would promote"
        if due is critical:
            say(f"autopromote: {critical['why']} — the critical-fix rule {verb} v{target}")
        else:
            say(f"autopromote: production last changed {weekly.get('days')} day(s) ago "
                f"({(weekly.get('production') or {}).get('date')}) — the weekly rule "
                f"{verb} v{target}")
        result = promote(project, target, requested_by=due["requested_by"],
                         notes_file=None, dry_run=not yes, log=say)
        result["rule"] = "critical" if due is critical else "weekly"
        result["production_before"] = due.get("production") or {}
        if due is critical:
            result["critical"] = critical.get("critical") or {}
    else:
        # `already_promoted` is a state-ledger shortcut for the armed path; the
        # two team-wide paths above already asked Play live, so they need it not.
        if already_promoted(state, target):
            return {"status": "already-promoted", "version": target,
                    "error": f"v{target} is already on production"}
        words = str(prov.get("requested_by") or "").strip()
        requested_by = (f"{words} — standing provision for this app "
                        f"(promote after CI, recorded {prov.get('at') or 'earlier'})")
        say(f"autopromote: v{target} — provisioned after CI; running the owner's ask now")
        result = promote(project, target, requested_by=requested_by,
                         notes_file=None, dry_run=not yes, log=say)
        result["provision"] = {"after_ci": True, "requested_by": words,
                               "at": prov.get("at") or ""}
    if yes or result.get("status") in ("refused", "ERROR", "held"):
        _record(project, state_of(project), result)
    return result


def line(project: Path) -> str:
    """One status line: is this app armed, and by whose words.

    Offline by design (it is printed by `status` on every call): the weekly rule
    is stated, not measured here — `autopromote <app> --run` does the live read.
    """
    prov = provision_of(Path(project))
    rule = weekly_rule()
    weekly = (f"; weekly rule: newest release candidate goes out when production is "
              f"more than {rule.get('max_days')} days old (read live from Play)"
              if rule.get("max_days") else "")
    if critical_rule().get("enabled"):
        weekly += ("; critical fix: a release candidate marked `rc --yes --critical` "
                   "goes out inside that week, with its author and why recorded")
    if prov.get("after_ci"):
        words = str(prov.get("requested_by") or "").strip()
        return (f"promote:    PROVISIONED after CI — newest release candidate goes to "
                f"production (owner: \"{words}\", {prov.get('at') or 'date unknown'})"
                + weekly)
    if prov.get("history"):
        return (f"promote:    OFF — the promotion-after-CI provision was removed "
                f"{prov.get('off_at') or ''}".rstrip() + "; the after-CI trigger is "
                "manual again" + weekly)
    return ("promote:    manual — no after-CI provision; "
            f"`devteam autopromote <name> --on --requested-by \"<owner words>\"` arms it"
            + weekly)
