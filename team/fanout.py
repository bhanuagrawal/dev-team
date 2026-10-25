"""The FAN-OUT: one entry, N developers, ONE gate and ONE release.

The default is still ONE developer. A fan-out exists because a large entry is
often N genuinely separate jobs (two screens; a seam plus the screen that uses
it) and the developer stage dominates wall-clock (≈11 min) while the release
train is serial anyway. The owner's constraint shapes all of it: **one version
bump and one release, after every agent is finished** — the agents are parallel
only in HOW the code is written, never in how it ships.

What makes that safe is that the split is a CLAIM, and claims are checked in
code BEFORE an agent is spawned (an LLM decides that an entry LOOKS splittable;
that decision is worthless if the split can be wrong silently):

  * segments are pairwise DISJOINT by path prefix — a segment owns a directory
    or a literal file, never an ambiguous mid-pattern glob whose overlap cannot
    be proven;
  * the union is INSIDE the parent's own allow-list — a segment subdivides the
    task, it can never widen the scope the task declared;
  * at least 2, at most MAX_SEGMENTS (a fan-out of one is just a run);
  * every segment is worth a worktree (MIN_SEGMENT_EFFORT): subdividing a task
    into pieces smaller than one honest session buys merge risk, not speed;
  * a parent that leases a DEVICE is refused. The lease from `run-start` has a
    single owner and one emulator cannot be driven by two agents, so device work
    is serial BY CONSTRUCTION rather than by coordination between agents.

Then each agent gets its own worktree and its own spec, and the merge is a real
git merge of real commits. A conflict means the split was not disjoint in
practice: the WHOLE fan-out is discarded — branches deleted, the parent
worktree reset to its base — and the entry is redone serially. That is the only
honest outcome: hand-resolving a conflict here is an unreviewed second author
writing into work nobody assigned them, and the reviewer would be reading a
merge nobody designed.
"""
from __future__ import annotations

import json
from pathlib import Path

from .core import (git, now_iso, pipeline_dir, profile_of, save_state,
                   state_of, target_items)

MAX_SEGMENTS = 4
#: A segment smaller than this is not worth a worktree, a merge and the
#: conflict risk that comes with it — it is one honest developer session.
MIN_SEGMENT_EFFORT = 60


def _prefix(target: str) -> str:
    """A target's path prefix: 'a/b/**' → 'a/b', 'a/b/' → 'a/b', './x.kt' → 'x.kt'."""
    t = target.strip().strip("/")
    for suffix in ("/**", "/*"):
        if t.endswith(suffix):
            t = t[: -len(suffix)]
            break
    t = t.rstrip("/")
    return t[2:] if t.startswith("./") else t


def _has_mid_glob(target: str) -> bool:
    """True when a wildcard sits anywhere but as a trailing '/**' or '/*'.

    Prefix comparison is only sound for a directory prefix or a literal file. A
    pattern like `ui/*/Screen.kt` overlaps segments that look disjoint by
    prefix, so it cannot be accepted: the machine must be able to PROVE the
    split, and a pattern it cannot reason about makes the proof impossible.
    """
    t = target.strip().strip("/")
    for suffix in ("/**", "/*"):
        if t.endswith(suffix):
            t = t[: -len(suffix)]
            break
    return any(ch in t for ch in "*?[")


def overlaps(a: str, b: str) -> bool:
    """Could these two targets touch the same file? Conservative by prefix:
    one being a path-prefix of the other is an overlap (a segment may CREATE a
    file under its directory, so existing files alone cannot answer this)."""
    pa, pb = _prefix(a), _prefix(b)
    if pa == pb:
        return True
    return pa.startswith(pb + "/") or pb.startswith(pa + "/")


def under(child: str, parent: str) -> bool:
    """Is `child` inside `parent` (equal counts as inside)?"""
    pc, pp = _prefix(child), _prefix(parent)
    return pc == pp or pc.startswith(pp + "/")


def _split_message(seg: dict) -> str:
    return str(seg.get("label") or seg.get("id") or "?")


def validate(project: Path, parent: dict, split: dict) -> list[dict]:
    """Check the split and return the SEGMENT SPECS (parent fields inherited).

    Every refusal names the offending segment and what to do instead, because
    the next move is always one of two: fix the split, or run it as a single
    developer (the default, and never a failure)."""
    pid = parent.get("id")
    if not pid:
        raise SystemExit("fan-out parent spec has no 'id'")
    parent_targets = target_items((parent.get("surface") or {}).get("targets")
                                  or (parent.get("surface") or {}).get("target"))
    if not parent_targets:
        raise SystemExit("fan-out parent spec declares no surface targets — there is "
                         "nothing for segments to subdivide")
    if parent.get("needs"):
        raise SystemExit(
            f"fan-out refused: this task leases {parent['needs']} — ONE owner, and one "
            f"emulator cannot be driven by two agents. Device work is serial by "
            f"construction: run it as a single developer")

    raw = (split or {}).get("segments")
    if not isinstance(raw, list) or not raw:
        raise SystemExit("split has no 'segments' list — a fan-out needs 2+ segments")
    if len(raw) < 2:
        raise SystemExit("fan-out refused: ONE segment is just a run — spawn a single "
                         "developer (the default) instead")
    if len(raw) > MAX_SEGMENTS:
        raise SystemExit(
            f"fan-out refused: {len(raw)} segments > MAX_SEGMENTS ({MAX_SEGMENTS}) — "
            f"more parallel worktrees buy merge risk and reviewer load, not speed; "
            f"split this entry into sequential queue entries instead")

    segs = []
    for i, seg in enumerate(raw, 1):
        if not isinstance(seg, dict):
            raise SystemExit(f"segment #{i} is not an object")
        label = str(seg.get("label") or "").strip()
        if not label:
            raise SystemExit(f"segment #{i} has no 'label'")
        targets = target_items(seg.get("targets") or seg.get("target"))
        if not targets:
            raise SystemExit(f"segment '{label}' names no targets — that field becomes "
                             f"the developer's edit allow-list, so it cannot be prose")
        for t in targets:
            if " " in t:
                raise SystemExit(f"segment '{label}' target '{t}' contains a space — "
                                 f"one path or glob per item")
            if _has_mid_glob(t):
                raise SystemExit(
                    f"segment '{label}' target '{t}' uses a MID-PATTERN glob — the "
                    f"machine cannot prove it disjoint from the other segments. Use a "
                    f"directory prefix ('.../ui/**'), a trailing '/*', or a literal file")
        if seg.get("needs"):
            raise SystemExit(f"segment '{label}' declares needs {seg['needs']} — resources "
                             f"are leased by the RUN (run-start), not by a segment")
        for t in targets:
            if not any(under(t, pt) for pt in parent_targets):
                raise SystemExit(
                    f"segment '{label}' targets '{t}', which is OUTSIDE the parent's "
                    f"allow-list ({', '.join(parent_targets)}) — a segment may "
                    f"subdivide the task, never widen it")
        effort = seg.get("effort_min")
        if effort is None:
            effort = max(1, int(parent.get("effort_min") or 0) // len(raw) or
                         MIN_SEGMENT_EFFORT)
        try:
            effort = int(effort)
        except (TypeError, ValueError):
            raise SystemExit(f"segment '{label}' has a non-integer effort_min")
        if effort < MIN_SEGMENT_EFFORT:
            raise SystemExit(
                f"segment '{label}' is sized {effort} min — below the "
                f"{MIN_SEGMENT_EFFORT}-min segment floor. A fan-out buys wall-clock "
                f"only when each piece is a real session; run this entry as a single "
                f"developer, or split it into sequential queue entries")
        segs.append({"id": f"{pid}-s{i}", "label": label, "targets": targets,
                     "effort_min": effort})

    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            a, b = segs[i], segs[j]
            for ta in a["targets"]:
                for tb in b["targets"]:
                    if overlaps(ta, tb):
                        raise SystemExit(
                            f"fan-out REFUSED: segments '{a['label']}' ({ta}) and "
                            f"'{b['label']}' ({tb}) can touch the same files. Parallel "
                            f"agents writing one file is the conflict the split exists "
                            f"to prevent — make the targets disjoint, or run this entry "
                            f"as ONE developer (the default)")

    specs = []
    for seg in segs:
        spec = dict(parent)
        spec.update({
            "id": seg["id"],
            "surface": {**(parent.get("surface") or {}), "label":
                        f"{seg['label']} (segment {seg['id'].rsplit('-', 1)[-1]} of "
                        f"{len(segs)}: {(parent.get('surface') or {}).get('label', pid)})",
                        "targets": seg["targets"]},
            "effort_min": seg["effort_min"],
            "needs": [],
            "parent": pid,
            "segment": seg["id"],
            "reason": f"segment of {pid}: {seg['label']}",
        })
        specs.append(spec)
    return specs


def _fanout_dir(project: Path, pid: str) -> Path:
    return pipeline_dir(project) / "fanout" / pid


def _cleanup(project: Path, segments: list[dict], log) -> None:
    """Remove the worktrees + branches a fan-out created (best effort)."""
    for seg in segments:
        wt = seg.get("worktree")
        if wt:
            git(["worktree", "remove", "--force", wt], cwd=project, timeout=120)
            log(f"fanout: removed worktree {wt}")
        git(["branch", "-D", seg.get("branch") or f"dt/{seg['id']}"],
            cwd=project, timeout=60)


def start(project: Path, parent: dict, split: Path | dict, log) -> dict:
    """Validate the split, then create one worktree + one spec per segment."""
    from .developer import prepare_worktree
    if not isinstance(split, dict):
        split = json.loads(Path(split).read_text())
    specs = validate(project, parent, split)
    pid = parent["id"]

    git(["fetch", "origin"], cwd=project, timeout=120)
    base = git(["rev-parse", "origin/main"], cwd=project).stdout.strip()
    if not base:
        raise SystemExit("fan-out: cannot resolve origin/main")

    made: list[dict] = []
    try:
        for spec, raw in zip(specs, split["segments"]):
            prepared = prepare_worktree(project, spec["id"], log)
            if not prepared:
                raise SystemExit(f"fan-out: worktree for segment {spec['id']} failed")
            wt, wt_base = prepared
            if wt_base != base:
                raise SystemExit(
                    f"fan-out: origin/main moved during setup ({wt_base[:8]} != "
                    f"{base[:8]}) — every segment must start from ONE base or the "
                    f"merge compares different worlds. Re-run worktree-start and "
                    f"fan-out-start; if main keeps moving, run this entry serially")
            made.append({"id": spec["id"],
                         "label": str(raw.get("label") or spec["id"]),
                         "targets": spec["surface"]["targets"],
                         "effort_min": spec["effort_min"],
                         "branch": f"dt/{spec['id']}", "worktree": str(wt),
                         "base": wt_base})
    except SystemExit:
        _cleanup(project, made, log)
        raise

    fdir = _fanout_dir(project, pid)
    (fdir / "segments").mkdir(parents=True, exist_ok=True)
    # Keep the split that was ACCEPTED next to the run: if a conflict later
    # discards the fan-out, the discarded plan is still on disk as evidence.
    (fdir / "split.json").write_text(json.dumps(split, indent=2) + "\n")
    out = []
    for spec, seg in zip(specs, made):
        spec["worktree"] = seg["worktree"]
        spec["branch"] = seg["branch"]
        p = fdir / "segments" / f"{spec['id']}.json"
        p.write_text(json.dumps(spec, indent=2) + "\n")
        out.append({**seg, "spec": str(p)})

    state = state_of(project)
    state["fanout"] = {"parent": pid, "created": now_iso(), "base": base,
                       "split": str(fdir / "split.json"), "segments": out}
    save_state(project, state)
    return {"parent": pid, "base": base, "segments": out}


def _discard(project: Path, state: dict, fo: dict, log, why: str,
             conflicts: list[str] | None = None) -> None:
    """Undo a fan-out completely: no branches, no worktrees, parent at base."""
    _cleanup(project, fo.get("segments") or [], log)
    parent_wt = project / ".pipeline" / "worktrees" / str(fo.get("parent"))
    if parent_wt.exists() and fo.get("base"):
        git(["reset", "--hard", fo["base"]], cwd=parent_wt, timeout=120)
    fo["result"] = "discarded: " + why
    if conflicts:
        fo["conflicts"] = conflicts
    state["fanout"] = fo
    save_state(project, state)  # a discard is EVIDENCE, never a silent reset
    log(f"fanout DISCARDED: {why}")


def merge(project: Path, parent: dict, log) -> dict:
    """Commit each segment and merge them into the parent worktree — or discard
    the whole fan-out. The parent worktree is the ONE tree that gets gated and
    released, so this is where N becomes 1 again."""
    state = state_of(project)
    fo = state.get("fanout") or {}
    pid = parent.get("id")
    if fo.get("parent") != pid:
        raise SystemExit(f"no fan-out recorded for '{pid}' — run fan-out-start first")
    segments = fo.get("segments") or []
    if len(segments) < 2:
        raise SystemExit("fan-out record has fewer than 2 segments — nothing to merge")
    parent_wt = project / ".pipeline" / "worktrees" / str(pid)
    if not parent_wt.exists():
        raise SystemExit(f"parent worktree missing: {parent_wt} — fan-out merges into the "
                         f"run's own worktree; create it with worktree-start and redo")

    if git(["status", "--porcelain"], cwd=parent_wt).stdout.strip():
        raise SystemExit(f"parent worktree is DIRTY ({parent_wt}) — it must be clean to "
                         f"merge into; that tree is what gets released")

    for seg in segments:
        wt = Path(seg["worktree"])
        if not wt.exists():
            _discard(project, state, fo, log, f"segment worktree missing: {wt}")
            raise SystemExit(f"fan-out discarded: segment '{seg['label']}' worktree is "
                             f"gone — redo the entry serially")
        if not git(["status", "--porcelain"], cwd=wt).stdout.strip():
            _discard(project, state, fo, log,
                     f"segment '{seg['label']}' produced NO changes")
            raise SystemExit(
                f"fan-out DISCARDED: segment '{seg['label']}' produced no changes at "
                f"all — the split carved out work that was not there (or that agent "
                f"did nothing). Run this entry as ONE developer; a fan-out where a "
                f"segment is a no-op makes the other segments' work look whole")
        git(["add", "-A"], cwd=wt, timeout=120)
        r = git(["commit", "-m", f"fanout({seg['id']}): {seg['label']}"],
                cwd=wt, timeout=120)
        if r.returncode != 0:
            _discard(project, state, fo, log, f"commit failed in segment '{seg['label']}'")
            raise SystemExit(f"fan-out DISCARDED: could not commit segment "
                             f"'{seg['label']}': {(r.stdout + r.stderr)[-400:]}")
        # THE ALLOW-LIST, MADE MECHANICAL. The developer prompt only ASKS an agent
        # to stay inside its targets; two agents running at once cannot be trusted
        # with an ask. A shared file touched by one segment is a shared file
        # touched by every segment — the conflict the split exists to prevent —
        # so leaving the lane discards the fan-out even when the merge would have
        # been clean.
        touched = [f for f in git(["diff", "--name-only", fo["base"], "HEAD"],
                                  cwd=wt).stdout.splitlines() if f.strip()]
        stray = [f for f in touched
                 if not any(under(f, t) for t in seg["targets"])]
        if stray:
            listed = ", ".join(stray[:5]) + (" …" if len(stray) > 5 else "")
            _discard(project, state, fo, log,
                     f"segment '{seg['label']}' edited outside its targets: {listed}")
            raise SystemExit(
                f"fan-out DISCARDED: segment '{seg['label']}' edited {listed}, which is "
                f"OUTSIDE its own targets ({', '.join(seg['targets'])}). A parallel "
                f"agent may write ONLY its segment: a shared file (a build script, a "
                f"strings table, a registry) touched by one agent is one every agent "
                f"must be assumed to touch, and that is the merge conflict this split "
                f"exists to prevent. Run this entry as ONE developer — the default")

    merged_shas: list[str] = []
    for seg in segments:
        r = git(["merge", "--no-ff", "--no-edit", seg["branch"]],
                cwd=parent_wt, timeout=180)
        if r.returncode != 0:
            conflicts = git(["diff", "--name-only", "--diff-filter=U"],
                            cwd=parent_wt).stdout.split()
            git(["merge", "--abort"], cwd=parent_wt, timeout=120)
            _discard(project, state, fo, log,
                     f"merge conflict on {', '.join(conflicts) or 'unknown files'}",
                     conflicts)
            raise SystemExit(
                f"fan-out DISCARDED: merging '{seg['label']}' CONFLICTED on "
                f"{', '.join(conflicts) or 'unknown files'}. The split was not disjoint "
                f"in practice, so the whole fan-out is gone (branches deleted, the "
                f"parent worktree is back at its base). Run this entry as ONE "
                f"developer — do not hand-resolve: nobody assigned that merge, and the "
                f"reviewer would be reading a change no one designed")
        sha = git(["rev-parse", "HEAD"], cwd=parent_wt).stdout.strip()
        merged_shas.append(sha)

    fo["result"] = "merged"
    fo["merged"] = merged_shas
    state["fanout"] = fo
    save_state(project, state)
    return {"merged": len(merged_shas), "parent": pid,
            "worktree": str(parent_wt), "commits": merged_shas,
            "next": f"verify --worktree {parent_wt} --task-spec "
                    f".pipeline/tasks/{pid}.json  (ONE gate on the MERGED tree), "
                    f"then review once and release once"}
