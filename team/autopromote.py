"""A recorded owner ask, executed after CI: promotion is still a verb.

WHY THIS EXISTS (owner directive 2026-11-17)
--------------------------------------------
"promote to production after ci complete always — if release candidate — create
a provision for doing this for an app when user ask in dev team"

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

from datetime import datetime, timezone
from pathlib import Path

from .core import (profile_of, profile_root, save_json, save_state, state_of)
from .promote import promote

LOG_KEY = "promote_provision_runs"
HISTORY_KEEP = 20


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
        "provisioned_by": (provision_of(project).get("requested_by") or ""),
    })
    state[LOG_KEY] = rows[-50:]
    save_state(project, state)


def run(project: Path, *, version: str = "", yes: bool = False,
        log=None) -> dict:
    """Execute the provision for one app.

    Benign no-ops are statuses, not failures: an app with no provision, an app
    whose build is already live, and an app whose newest build has not reached
    `internal` yet all return normally so the nightly keeps going. A real
    refusal (an `rc --no` veto, a missing note, a Play error) is reported with
    the reason in the same dict.
    """
    project = Path(project)
    say = log or (lambda *_: None)
    prov = provision_of(project)
    if not prov.get("after_ci"):
        return {"status": "not-provisioned",
                "error": "this app has no owner provision for promotion after CI — "
                         f"`devteam autopromote {project.name} --on "
                         "--requested-by \"<the owner's words>\"` arms it"}

    state = state_of(project)
    target = str(version or "").strip() or candidate(state)
    if not target:
        return {"status": "nothing-to-promote",
                "error": "the ledger names no shipped version yet"}

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
    """One status line: is this app armed, and by whose words."""
    prov = provision_of(Path(project))
    if prov.get("after_ci"):
        words = str(prov.get("requested_by") or "").strip()
        return (f"promote:    PROVISIONED after CI — newest release candidate goes to "
                f"production (owner: \"{words}\", {prov.get('at') or 'date unknown'})")
    if prov.get("history"):
        return (f"promote:    OFF — the promotion-after-CI provision was removed "
                f"{prov.get('off_at') or ''}".rstrip() + "; promotion is manual again")
    return ("promote:    manual — no after-CI provision; "
            f"`devteam autopromote <name> --on --requested-by \"<owner words>\"` arms it")
