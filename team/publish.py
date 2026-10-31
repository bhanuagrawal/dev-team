"""Publish the plan: put `docs/plan.md` + `docs/plan.html` on the project's main.

Why this exists: `.pipeline/` is excluded repo-locally (`.git/info/exclude`), so
the goal tree lived only on the machine that generated it — the owner asked for
the plan to be *pushed*, shared and viewable where the repo lives. The two files
under `docs/` are deterministic renders of that same tree (team/tree.py, no LLM),
so the shared copy can never drift from the local one.

Best-effort BY DESIGN: publishing is bookkeeping, not delivery. No network, a
moved remote, an origin that does not know the branch — each reports WHY and
leaves the run alone; the next run retries. Nothing here is ever allowed to fail
a run.

The commit is built in a DETACHED WORKTREE sitting on origin/<main> (the same
idiom the shipper and the codebase scanner use), never in the owner's checkout.
That distinction is the whole fix for a project whose local branch has diverged
from origin or whose working tree carries the owner's uncommitted work: an
in-place publish could only ever succeed if the branch was a clean fast-forward
and the tree clean enough to rebase, so it failed forever and silently while the
owner never got a `shared plan:` link.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .core import git, profile_of, state_of

# The committed mirror. Order matters only for the commit message.
FILES = ("docs/plan.md", "docs/plan.html")

# A throwaway worktree pinned to origin/<main>, where the plan commit is built.
PUBLISH_TREE = ".pipeline/worktrees/.publish"


def github_url(project: Path, profile: dict | None = None) -> str | None:
    """Where the owner (or anyone) can read the plan on GitHub, or None when the
    profile has no repo slug to build it from."""
    profile = profile or profile_of(project)
    slug = str(profile.get("repo_slug") or "").strip().strip("/")
    branch = str(profile.get("main_branch") or "main")
    if "/" not in slug:
        return None
    return f"https://github.com/{slug}/blob/{branch}/docs/plan.md"


def _g(args: list[str], project: Path, timeout: int = 60):
    return git(args, cwd=project, timeout=timeout)


def published_url(project: Path) -> tuple[str | None, str]:
    """(url, why-not) for the plan as it stands — LOCAL refs only, no network, so
    it is safe to call from a read-only command like `status`.

    The question is "is what I render NOW the same plan origin/<main> serves?"
    — CONTENT, not local branch topology. The local checkout may legitimately sit
    on other commits (or another branch, or carry WIP) while the shared copy is
    perfectly current; asking HEAD whether it is an ancestor of origin/main
    answered a different question and reported "not published" forever after any
    local commit.
    """
    profile = profile_of(project)
    branch = str(profile.get("main_branch") or "main")
    if not (project / FILES[0]).exists():
        return None, "the plan mirror has not been written yet"
    ref = f"origin/{branch}"
    if _g(["rev-parse", "--verify", "--quiet", ref], project, timeout=30).returncode != 0:
        return None, f"{ref} is not known locally yet (try `git fetch`)"
    for f in FILES:
        there = _g(["rev-parse", "--verify", "--quiet", f"{ref}:{f}"], project, timeout=30)
        if there.returncode != 0:
            return None, f"the plan is not on {ref} yet ({f} is missing there)"
        local = _g(["hash-object", "--", f], project, timeout=30)
        if (local.stdout or "").strip() != (there.stdout or "").strip():
            return None, "the local plan has changes that are not on origin yet"
    return github_url(project, profile), ""


def _origin_tree(project: Path, branch: str) -> tuple[Path | None, str]:
    """A detached worktree sitting exactly on origin/<branch> — where the plan
    commit is BUILT. Returns (path, why-not); best-effort like everything here.
    """
    wt = project / PUBLISH_TREE
    _g(["fetch", "origin"], project, timeout=120)
    base = f"origin/{branch}"
    if _g(["rev-parse", "--verify", "--quiet", base], project, timeout=30).returncode != 0:
        return None, f"{base} is not known locally (no network?)"
    r = None
    if (wt / ".git").exists():
        r = _g(["checkout", "--detach", "--force", base], wt, timeout=120)
        if r.returncode == 0:
            _g(["clean", "-fd"], wt, timeout=60)
        else:
            # A stale registration (pruned metadata, moved directory) would
            # otherwise fail every publish from now on: throw it away and re-add.
            _g(["worktree", "remove", "--force", str(wt)], project, timeout=120)
            _g(["worktree", "prune"], project, timeout=60)
            shutil.rmtree(wt, ignore_errors=True)
            r = None
    if r is None:
        if wt.exists():  # a leftover directory with no live registration
            shutil.rmtree(wt, ignore_errors=True)
            _g(["worktree", "prune"], project, timeout=60)
        r = git(["worktree", "add", "--detach", str(wt), base], cwd=project, timeout=300)
        _g(["clean", "-fd"], wt, timeout=60)
    if r.returncode != 0:
        tail = (r.stdout + r.stderr).strip().splitlines()
        return None, "cannot prepare the publish worktree: " + (tail[-1] if tail else "unknown")
    return wt, ""


def _subject(project: Path) -> str:
    """A commit subject that says what the pointer is pointing at, from the data."""
    from . import tree as tree_mod
    col = tree_mod._collect(tree_mod.build(project))
    return (f"chore(plan): {len(col['branches'])} goals, {len(col['queue'])} queued, "
            f"{len(col['done']) + len(col['earlier'])} shipped")


def _message(project: Path) -> str:
    from . import tree as tree_mod
    model = tree_mod.build(project)
    goal = str(model["root"].get("title") or "").strip().replace("\n", " ")
    return (
        _subject(project) + "\n\n"
        "The plan the team is working toward, rendered from this project's own\n"
        "records (.pipeline/, machine-local) so it travels with the repo:\n"
        "  docs/plan.md    — the owner-readable view (this file)\n"
        "  docs/plan.html  — the same plan as a clickable page\n\n"
        f"Goal: {goal[:200]}\n\n"
        "Written by the dev-team pipeline on every run; do not edit by hand.\n"
    )


def publish(project: Path, log=None) -> dict:
    """Commit the plan mirror and push it to origin/<main>.

    Returns {pushed, url, why, sha}. `pushed` False always carries a `why` the
    owner can act on, and never means the run failed.

    The commit is built in a detached worktree on origin/<main> and pushed from
    there, so it is a child of origin by construction: a diverged local branch or
    the owner's uncommitted work cannot block bookkeeping, and nothing in their
    checkout is ever read, rebased, stashed or reset.
    """
    profile = profile_of(project)
    branch = str(profile.get("main_branch") or "main")
    if not (project / FILES[0]).exists():
        return {"pushed": False, "url": None, "sha": "",
                "why": "no plan mirror to publish yet"}

    # Already published and unchanged: no commit, no push, and we can say where
    # it lives instead of silently doing nothing.
    url, _why = published_url(project)
    if url:
        return {"pushed": True, "url": url, "sha": "",
                "why": "unchanged since the last publish"}

    if log:
        log(f"publishing the plan ({FILES[0]} → origin/{branch})")
    note = _message(project)
    for _attempt in range(1, 4):
        wt, why = _origin_tree(project, branch)
        if wt is None:
            return {"pushed": False, "url": None, "sha": "", "why": why}
        for f in FILES:
            (wt / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(project / f, wt / f)
        _g(["add", "--", *FILES], wt, timeout=60)
        if _g(["diff", "--cached", "--quiet", "--", *FILES], wt, timeout=60).returncode == 0:
            # origin already carries exactly this plan (a run published it first).
            url, why = published_url(project)
            return {"pushed": True, "url": url or github_url(project, profile),
                    "sha": "", "why": why or "already on origin"}
        c = _g(["commit", "-m", note, "--", *FILES], wt, timeout=120)
        if c.returncode != 0:
            tail = (c.stderr or c.stdout or "").strip().splitlines()
            return {"pushed": False, "url": None, "sha": "",
                    "why": f"the plan commit failed: {tail[-1] if tail else 'unknown'}"}
        sha = (_g(["rev-parse", "--short", "HEAD"], wt, timeout=30).stdout or "").strip()
        r = _g(["push", "origin", f"HEAD:{branch}"], wt, timeout=180)
        if r.returncode == 0:
            url, why = published_url(project)
            if url:
                return {"pushed": True, "url": url, "sha": sha, "why": "published"}
            # Pushed, but the local check disagrees (a later render landed after
            # ours). Still honest: the mirror is on origin, its URL is real.
            return {"pushed": True, "url": github_url(project, profile), "sha": sha,
                    "why": f"pushed ({why})"}
        if log:
            log("push rejected — origin moved; rebuilding the plan commit on the new tip")
    return {"pushed": False, "url": None, "sha": "",
            "why": "the push was rejected 3 times — the plan commit was not needed "
                   "on origin; the next run retries"}


def commit_paths(project: Path, paths: list[str], message: str, log=None) -> dict:
    """Commit the LOCAL versions of `paths` onto origin/<main>, from the anchored
    worktree. Generic form of `publish()` for any command that writes into the
    project directory (store assets, harness scaffolding, listing edits).

    The checkout is never staged, rebased or reset: the identical bytes are
    copied into a detached worktree sitting on origin, committed there and
    pushed. So a dirty or diverged checkout cannot block the commit, and the
    owner's own WIP is never swept into it — callers pass only the paths their
    command touched.

    Returns {pushed, sha, why}. `pushed: False` always carries a `why` and is
    never fatal: the next run retries.
    """
    say = log or (lambda *_: None)
    profile = profile_of(project)
    branch = str(profile.get("main_branch") or "main")
    # `.pipeline/` is machine-local by contract (repo-local exclude) — never
    # commit it, even if a caller passes a path inside it.
    keep = [str(p).strip() for p in paths
            if str(p).strip() and not str(p).strip().startswith(".pipeline/")]
    if not keep:
        return {"pushed": False, "sha": "", "why": "no committable paths"}
    for _attempt in range(1, 4):
        wt, why = _origin_tree(project, branch)
        if wt is None:
            return {"pushed": False, "sha": "", "why": why}
        for rel in keep:
            src, dst = project / rel, wt / rel
            if src.is_dir():
                # A caller that hands over a directory means "this whole tree is
                # mine": copy what the checkout has and drop what it no longer has
                # (a storefront frame the review cut must disappear from origin, not
                # linger as a file nobody renders). Scoped to the named path, so the
                # owner's unrelated WIP is still untouched.
                dst.mkdir(parents=True, exist_ok=True)
                for f in sorted(src.rglob("*")):
                    if not f.is_file():
                        continue
                    target = dst / f.relative_to(src)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target)
                for f in sorted(dst.rglob("*"), reverse=True):
                    if f.is_file() and not (src / f.relative_to(dst)).exists():
                        f.unlink()
            elif src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            elif dst.exists():
                _g(["rm", "-q", "--", rel], wt, timeout=60)
        _g(["add", "-A", "--", *keep], wt, timeout=120)
        if _g(["diff", "--cached", "--quiet", "--", *keep], wt, timeout=60).returncode == 0:
            return {"pushed": True, "sha": "", "why": "already on origin"}
        c = _g(["commit", "-m", message, "--", *keep], wt, timeout=120)
        if c.returncode != 0:
            tail = (c.stderr or c.stdout or "").strip().splitlines()
            return {"pushed": False, "sha": "",
                    "why": f"commit failed: {tail[-1] if tail else 'unknown'}"}
        sha = (_g(["rev-parse", "--short", "HEAD"], wt, timeout=30).stdout or "").strip()
        r = _g(["push", "origin", f"HEAD:{branch}"], wt, timeout=180)
        if r.returncode == 0:
            say(f"committed {len(keep)} path(s) → origin/{branch} @ {sha}")
            return {"pushed": True, "sha": sha, "why": "committed"}
        say("push rejected — origin moved; rebuilding the commit on the new tip")
    return {"pushed": False, "sha": "", "why": "push rejected 3 times"}


def line(project: Path, res: dict) -> str:
    """One owner-facing line about the shared plan."""
    if res.get("pushed") and res.get("url"):
        return f"shared plan: {res['url']}   ({res.get('why') or 'published'})"
    return f"shared plan: NOT pushed — {res.get('why') or 'unknown reason'}"
