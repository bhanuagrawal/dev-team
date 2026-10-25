"""The REVIEW stage — dev plus QA.

Each role in this team is a stage with a declared setup requirement that `doctor`
validates. `developer` had one; `reviewer` was declared in
config/runtimes.json and NOTHING invoked it, so the pipeline's only check on a
change was mechanical: does it compile, do the tests pass, is it in scope, did it
touch a knob, did it add a dependency. All of that is real and all of it is blind
to the one question a second pair of eyes exists for — is this change actually
RIGHT?

So this module builds the prompt for an INDEPENDENT reviewer (a fresh session
that did not write the change) and records its verdict. The reviewer is not a
lint pass: it is told what the gates already prove, so it spends its attention on
what they cannot see — an implementation that compiles and passes tests and still
does the wrong thing, a shipped promise quietly broken, a failure swallowed, a
claim the code does not support.

The verdict is BINDING by default (owner's direction, 2026-10-25: teams work like
dev plus QA), but it is bounded the same way the gate failures are: a `fix` gets
the developer ONE more attempt, twice in total, and then the run records
`blocked` and raises the owner ask rather than looping. A project can set
`profile['review'] = {"advisory": true}` to record verdicts without letting them
stop a release.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .core import git, now_iso

# A diff has to fit in a prompt. Past these bounds the reviewer gets the file
# list and a truncation notice instead of a silently half-read change — a review
# of a change the reviewer could not see is worse than no review. Raised to 2500
# after a real 240-minute entry truncated at 1400 (~28k tokens, modest for a
# review) — the biggest entries are the ones worth reviewing most.
MAX_DIFF_LINES = 2500
MAX_FILE_CHARS = 24000
MAX_UNTRACKED_FILES = 40

VERDICTS = ("ship", "fix", "block")

CHECKLIST = (
    "1. DOES IT DO WHAT THE TASK SAID — exactly, and no more? Adjacent work, "
    "unrelated refactors and 'while I was here' edits are findings, not bonuses.",
    "2. DOES IT BREAK SOMETHING THAT ALREADY SHIPPED? The gates prove the tests "
    "that EXIST still pass; they cannot know an invariant nobody wrote down. "
    "Look for a shipped behaviour the change quietly changes.",
    "3. DOES IT CLAIM MORE THAN THE CODE DOES? A UI that asserts a fact the data "
    "cannot support, an error path that reports success, a 'saved' that may not "
    "have saved — this team has shipped exactly that class of defect before.",
    "4. IS A FAILURE SWALLOWED? `runCatching {}` with a silent fallback, a "
    "catch that drops the error, a default that hides a broken state.",
    "5. WHAT HAPPENS ON THE UNHAPPY PATH? empty/zero/one item, a short screen, a "
    "missing permission, a cancelled action, a second tap while the first is "
    "still running.",
    "6. WHAT IS LEFT BEHIND? dead code, a TODO, a debug artifact, an unused "
    "parameter, a comment that no longer matches the code.",
)


def _task_brief(task: dict) -> str:
    surface = task.get("surface") or {}
    targets = surface.get("targets") or []
    if isinstance(targets, str):
        targets = [targets]
    return (
        f"  label:    {surface.get('label')}\n"
        f"  type:     {task.get('type')}\n"
        f"  advances: {task.get('advances')}\n"
        f"  targets:  {', '.join(targets)}\n"
        f"  reason:   {task.get('reason')}"
    )


def collect_diff(worktree: Path, base: str) -> tuple[str, bool]:
    """The whole change as text: the tracked diff PLUS the untracked files.

    An uncommitted new file does not appear in `git diff`, and the developer is
    told to leave changes uncommitted — so a reviewer fed only `git diff` would
    silently not see the new files, which is most of the change. Nothing here
    mutates the index (`git add -N` would, and would also hide the files from the
    release's untracked-file warning)."""
    out, truncated = [], False
    stat = git(["diff", base, "--stat"], cwd=worktree, timeout=120).stdout
    if stat.strip():
        out.append("TRACKED CHANGES\n" + stat.rstrip())
    diff = git(["diff", base], cwd=worktree, timeout=180).stdout
    if diff.strip():
        out.append("DIFF\n" + diff.rstrip())

    untracked = [f for f in git(["ls-files", "--others", "--exclude-standard"],
                                cwd=worktree, timeout=60).stdout.splitlines() if f.strip()]
    for rel in untracked[:MAX_UNTRACKED_FILES]:
        p = worktree / rel
        try:
            body = p.read_text(errors="replace")
        except OSError:
            continue
        if len(body) > MAX_FILE_CHARS:
            body, truncated = body[:MAX_FILE_CHARS] + "\n… [file truncated]", True
        out.append(f"NEW FILE (untracked): {rel}\n{body.rstrip()}")
    if len(untracked) > MAX_UNTRACKED_FILES:
        truncated = True
        out.append(f"… [{len(untracked) - MAX_UNTRACKED_FILES} more untracked files not shown]")

    text = "\n\n".join(out)
    lines = text.splitlines()
    if len(lines) > MAX_DIFF_LINES:
        text = "\n".join(lines[:MAX_DIFF_LINES]) + \
            f"\n\n… [DIFF TRUNCATED at {MAX_DIFF_LINES} of {len(lines)} lines — " \
            f"review what you can see and say plainly that you could not see the rest]"
        truncated = True
    return text, truncated


def build_prompt(project: Path, profile: dict, task: dict, worktree: Path,
                 base: str, gates: str = "") -> str:
    """The review prompt. Handed to a FRESH session — never the developer's."""
    diff, truncated = collect_diff(worktree, base)
    gate_line = gates.strip() or (
        "(the mechanical gates passed — assume compile, the existing tests, scope, "
        "the release knobs and the dependency declaration are all green; they are "
        "NOT your concern unless the diff contradicts them)")
    return f"""You are the REVIEWER role of the dev-team pipeline — the QA half of "dev plus QA".

You did NOT write this change, and that is the point: someone must look at it
without having decided it was good. You are not a linter and not a style police.
Your job is the question every mechanical gate is blind to: **is this change
RIGHT?** Compiling, passing the tests that exist, staying in scope, not touching
a release knob and not adding an undeclared dependency are all already proven —
do not re-litigate them.

THE TASK IT WAS MEANT TO DO
{_task_brief(task)}

{gate_line}

WHAT TO LOOK FOR (in order — the first four matter most)
{chr(10).join(CHECKLIST)}

THE CHANGE ({len(diff.splitlines())} lines of diff/files shown
{'; TRUNCATED — say so in your note if you could not see all of it' if truncated else ''})
{diff}

RESPOND WITH STRICT JSON ONLY (no prose, no code fence):
{{
  "verdict": "ship" | "fix" | "block",
  "findings": [
    {{"severity": "high|medium|low", "file": "<path from the diff>",
      "what": "what is wrong, concretely", "why": "the consequence for a user"}}
  ],
  "note": "one line: the single reason for this verdict"
}}

HOW TO CHOOSE THE VERDICT
- "ship": the change does what the task said and you found nothing that would
  harm a user. Findings may still be listed (low severity) — they do not block.
- "fix": something IS wrong and it is fixable in this change. Name the file and
  what to change; be specific enough that the fix can be made without you.
- "block": the change would harm users, break a shipped promise, or cannot be
  judged from what you were shown. Use it rarely and say exactly why.

RULES
- Report ONLY what the diff shows. Do not invent context you were not given, and
  do not ask for files you were not shown — say you could not see it instead.
- No style nitpicks, no preference-only findings, no "consider extracting".
- An empty findings list with "ship" is a perfectly good answer. Do not
  manufacture a finding to look thorough: a false `fix` costs a whole extra
  release cycle, and a rubber-stamp `ship` costs the trust that put you here."""


def parse_verdict(text: str) -> dict:
    """The reviewer's JSON verdict, or a hard error — never a silent default.

    A failed parse must not become a `ship`: that is precisely the failure mode
    this stage exists to prevent, so it raises instead."""
    body = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", body, re.S)
    if fenced:
        body = fenced.group(1).strip()
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        raise SystemExit("reviewer verdict could not be parsed — no JSON object in "
                         "the response; re-run the reviewer (never assume a ship)")
    data = json.loads(body[start:end + 1])
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        raise SystemExit(f"reviewer verdict '{verdict}' is not one of {VERDICTS} — "
                         f"re-run the reviewer (never assume a ship)")
    findings = data.get("findings") or []
    if not isinstance(findings, list):
        findings = [findings]
    return {
        "verdict": verdict,
        "findings": findings,
        "note": str(data.get("note") or "").strip(),
    }


def review_policy(profile: dict) -> dict:
    cfg = profile.get("review") or {}
    rounds = cfg.get("max_rounds")
    try:
        rounds = int(rounds) if rounds is not None else 2
    except (TypeError, ValueError):
        rounds = 2
    return {
        "advisory": bool(cfg.get("advisory")),
        "max_rounds": max(1, min(rounds, 5)),
    }


def record_review(state: dict, task: dict, verdict: dict, worktree: str = "") -> dict:
    """Append the verdict to the state's review log (never edits an entry)."""
    entry = {
        "at": now_iso(),
        "task_id": task.get("id"),
        "label": (task.get("surface") or {}).get("label"),
        "verdict": verdict["verdict"],
        "note": verdict.get("note", ""),
        "findings": verdict.get("findings") or [],
        "worktree": worktree,
    }
    state.setdefault("reviews", []).append(entry)
    return entry


def rounds_for(state: dict, task_id: str) -> int:
    return sum(1 for r in (state.get("reviews") or []) if r.get("task_id") == task_id)


def next_action(profile: dict, state: dict, task: dict, verdict: dict) -> str:
    """What the orchestrator does with this verdict — decided in code, printed."""
    policy = review_policy(profile)
    did = rounds_for(state, task.get("id"))
    v = verdict["verdict"]
    if policy["advisory"]:
        return (f"ADVISORY MODE (profile.review.advisory): verdict '{v}' is recorded "
                f"and does NOT stop the release — proceed as if it were 'ship'.")
    if v == "ship":
        return "verdict ship → proceed to step 8b's release."
    if did >= policy["max_rounds"]:
        return (f"verdict '{v}' and the review budget is spent ({did}/{policy['max_rounds']}) "
                f"→ record --result blocked, raise the owner ask, do NOT release.")
    return (f"verdict '{v}' → spawn a FRESH developer session with the findings below "
            f"appended to its prompt (review round {did + 1} of {policy['max_rounds']} is "
            f"the re-review). Never re-run the reviewer on the same diff expecting a "
            f"different answer.")
