"""Publish the plan: put `docs/plan.md` + `docs/plan.html` on the project's main.

Why this exists: `.pipeline/` is excluded repo-locally (`.git/info/exclude`), so
the goal tree lived only on the machine that generated it — the owner asked for
the plan to be *pushed*, shared and viewable where the repo lives. The two files
under `docs/` are deterministic renders of that same tree (team/tree.py, no LLM),
so the shared copy can never drift from the local one.

Best-effort BY DESIGN: publishing is bookkeeping, not delivery. A checkout on
another branch, no network, a moved remote, a tree that cannot rebase — each
reports WHY and leaves the run alone; the next run retries. Nothing here is ever
allowed to fail a run.
"""
from __future__ import annotations

from pathlib import Path

from .core import git, profile_of, state_of

# The committed mirror. Order matters only for the commit message.
FILES = ("docs/plan.md", "docs/plan.html")


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

    The URL is only returned when it is true: the mirror exists, is tracked, has
    no unpublished changes, and the commit holding it is on origin/main.
    """
    profile = profile_of(project)
    branch = str(profile.get("main_branch") or "main")
    if not (project / FILES[0]).exists():
        return None, "the plan mirror has not been written yet"
    if _g(["ls-files", "--error-unmatch", "--", *FILES], project).returncode != 0:
        return None, "docs/plan.* exists but is not committed yet"
    if _g(["diff", "--quiet", "--", *FILES], project).returncode != 0:
        return None, "the local plan has unpushed changes"
    if _g(["diff", "--cached", "--quiet", "--", *FILES], project).returncode != 0:
        return None, "the plan is committed but not pushed yet"
    anc = _g(["merge-base", "--is-ancestor", "HEAD", f"origin/{branch}"], project)
    if anc.returncode != 0:
        return None, f"the plan is not on origin/{branch} yet"
    return github_url(project, profile), ""


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
    """
    profile = profile_of(project)
    branch = str(profile.get("main_branch") or "main")
    if not (project / FILES[0]).exists():
        return {"pushed": False, "url": None, "sha": "",
                "why": "no plan mirror to publish yet"}

    cur = _g(["rev-parse", "--abbrev-ref", "HEAD"], project, timeout=30)
    head = (cur.stdout or "").strip()
    if head != branch:
        return {"pushed": False, "url": None, "sha": "",
                "why": f"the local checkout is on '{head or '?'}', and the plan is "
                       f"only published from {branch}"}

    # Already published and unchanged: no commit, no push, and we can say where
    # it lives instead of silently doing nothing.
    url, _why = published_url(project)
    if url:
        return {"pushed": True, "url": url, "sha": "",
                "why": "unchanged since the last publish"}

    # Sync first so the push is usually a fast-forward instead of a rebase. Both
    # steps are best-effort: offline, we find out at push time anyway.
    if log:
        log("publishing the plan (docs/plan.md → origin/" + branch + ")")
    _g(["fetch", "origin"], project, timeout=120)
    _g(["merge", "--ff-only", f"origin/{branch}"], project, timeout=120)

    _g(["add", "--", *FILES], project)
    staged = _g(["diff", "--cached", "--quiet", "--", *FILES], project).returncode != 0
    if staged:
        c = _g(["commit", "-m", _message(project), "--", *FILES], project, timeout=120)
        if c.returncode != 0:
            tail = (c.stderr or c.stdout or "").strip().splitlines()
            return {"pushed": False, "url": None, "sha": "",
                    "why": f"the plan commit failed: {tail[-1] if tail else 'unknown'}"}

    sha = (_g(["rev-parse", "--short", "HEAD"], project, timeout=30).stdout or "").strip()
    r = _g(["push", "origin", f"HEAD:{branch}"], project, timeout=180)
    if r.returncode != 0:
        # Remote moved (a release landed while we were writing): rebase and retry,
        # exactly like the shipper does — or give up loudly and retry next run.
        _g(["fetch", "origin"], project, timeout=120)
        rb = _g(["rebase", f"origin/{branch}"], project, timeout=180)
        if rb.returncode != 0:
            _g(["rebase", "--abort"], project, timeout=60)
            return {"pushed": False, "url": None, "sha": sha,
                    "why": "the push was rejected and rebasing failed — the plan "
                           "commit stays local, the next run retries"}
        r = _g(["push", "origin", f"HEAD:{branch}"], project, timeout=180)
        if r.returncode != 0:
            return {"pushed": False, "url": None, "sha": sha,
                    "why": "the push failed twice — the plan commit stays local, "
                           "the next run retries"}

    url, why = published_url(project)
    if not url:
        # Pushed, but the local check disagrees (e.g. an unrelated later commit is
        # still local). Still honest: the mirror is on origin, its URL is real.
        return {"pushed": True, "url": github_url(project, profile), "sha": sha,
                "why": f"pushed ({why})"}
    return {"pushed": True, "url": url, "sha": sha, "why": "published"}


def line(project: Path, res: dict) -> str:
    """One owner-facing line about the shared plan."""
    if res.get("pushed") and res.get("url"):
        return f"shared plan: {res['url']}   ({res.get('why') or 'published'})"
    return f"shared plan: NOT pushed — {res.get('why') or 'unknown reason'}"
