"""Publish the plan: put `docs/goals.html` on the project's main.

Why this exists: `.pipeline/` is excluded repo-locally (`.git/info/exclude`), so
the goal tree lived only on the machine that generated it — the owner asked for
the plan to be *pushed*, shared and viewable where the repo lives, and for that
one page to be HOSTED rather than a markdown file read on github.com. The file
under `docs/` is the deterministic render of that same tree (team/tree.py, no
LLM), so the shared copy can never drift from the local one, and GitHub Pages
serves it at the repo's own site when Pages is configured for `/docs`.

The URL is only ever REPORTED after it has been read back (HTTP 200): a push is
not a page, and Pages builds on its own schedule. The verified URL is recorded
in `.pipeline/published.json` so `status` (read-only, offline) can print it
without a network call and without guessing.

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

import hashlib
import json
import shutil
import time
from pathlib import Path

from .core import git, load_json, now_iso, profile_of, run, save_json, state_of

# The committed mirror. Order matters only for the commit message.
FILES = ("docs/goals.html",)

# Files this mirror replaced. Publishing removes them from main: a stale
# markdown/HTML copy of the plan that no longer updates is worse than no copy.
RETIRED = ("docs/plan.md", "docs/plan.html")

# The verified-served record (machine-local): {url, site, sha, verified_at}.
RECORD = ".pipeline/published.json"

# A throwaway worktree pinned to origin/<main>, where the plan commit is built.
PUBLISH_TREE = ".pipeline/worktrees/.publish"


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def site_root(project: Path, profile: dict | None = None) -> str | None:
    """The GitHub Pages root this repo is served at, or None without a repo slug.

    GitHub's own answer wins (a custom domain or a Pages source other than
    `/docs` makes the conventional URL a guess); the convention is only the
    fallback when `gh` cannot answer.
    """
    profile = profile or profile_of(project)
    slug = str(profile.get("repo_slug") or "").strip().strip("/")
    if "/" not in slug:
        return None
    rec = load_json(project / RECORD) or {}
    if str(rec.get("site") or "").strip():
        return str(rec["site"]).rstrip("/")
    try:
        r = run(["gh", "api", f"repos/{slug}/pages"], project, timeout=30)
    except (FileNotFoundError, OSError):
        r = None  # gh not installed — the convention below is the honest fallback
    if r is not None and r.returncode == 0 and (r.stdout or "").strip():
        try:
            url = str(json.loads(r.stdout).get("html_url") or "").strip()
            if url.startswith("http"):
                return url.rstrip("/")
        except Exception:
            pass
    owner, repo = slug.split("/", 1)
    return f"https://{owner.lower()}.github.io/{repo}"


def pages_url(project: Path, profile: dict | None = None) -> str | None:
    """Where the hosted plan page lives (or will live once Pages builds it)."""
    root = site_root(project, profile)
    return f"{root}/goals.html" if root else None


def _fetch(url: str, timeout: int = 15) -> tuple[bool, str]:
    """GET the page. (ok, why-not) — a served page, not a promised one."""
    from urllib.request import Request, urlopen
    try:
        req = Request(url, headers={"User-Agent": "devteam-publish"})
        with urlopen(req, timeout=timeout) as r:
            head = r.read(8192)
            code = int(getattr(r, "status", 200) or 200)
            return (code == 200 and b"<html" in head.lower()), f"HTTP {code}"
    except Exception as e:  # 404 while Pages builds, DNS, TLS, timeout — all "not yet"
        return False, str(e)[:140]


def _verify_hosted(project: Path, url: str, sha: str,
                   attempts: int = 4, delay: float = 10.0) -> tuple[bool, str]:
    """Read the hosted page back, retrying through a Pages build."""
    why = ""
    for i in range(max(1, attempts)):
        ok, why = _fetch(url)
        if ok:
            save_json(project / RECORD, {"url": url, "site": url.rsplit("/", 1)[0],
                                         "sha": sha, "verified_at": now_iso()})
            return True, ""
        if i + 1 < attempts:
            time.sleep(delay)
    return False, why


def _result(project: Path, pushed: bool, sha: str = "", why: str = "",
            attempts: int = 4) -> dict:
    """A publish outcome that reports the URL only when the page answers."""
    url = pages_url(project)
    if not pushed:
        return {"pushed": False, "url": None, "sha": sha, "why": why}
    if not url:
        return {"pushed": True, "url": None, "sha": sha,
                "why": (why + " — " if why else "") + "no Pages URL is known for this repo"}
    ok, hwhy = _verify_hosted(project, url, _sha(project / FILES[0]), attempts=attempts)
    if ok:
        return {"pushed": True, "url": url, "sha": sha, "why": why or "published"}
    return {"pushed": True, "url": None, "sha": sha,
            "why": (why + " — " if why else "") +
                   f"pushed, but GitHub Pages has not served {FILES[0]} yet ({hwhy})"}


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
    # On origin — but a push is not a page. The URL is reported only for the
    # build this machine actually read back (see _verify_hosted); anything else
    # is a guess, and `status` has no network to check it with.
    rec = load_json(project / RECORD) or {}
    url = str(rec.get("url") or "").strip()
    if not url or str(rec.get("sha") or "") != _sha(project / FILES[0]):
        return None, ("the mirror is on origin; its hosted page has not been read "
                      "back yet (run `devteam publish`)")
    return url, ""


def _primary_worktree(project: Path) -> Path:
    """The checkout that OWNS `.git` — never a linked dev worktree.

    The publish tree is long-lived bookkeeping for the project, so it belongs to
    the primary checkout. When `project` is itself a dev worktree (which is how
    every ad-hoc command in a run is invoked), `<project>/.pipeline/worktrees/
    .publish` makes git register a worktree INSIDE a worktree: the inner one is
    deleted with the outer tree and leaves a registration pointing at nothing, and
    `git worktree list` shows a tree nobody can prune.
    """
    r = _g(["rev-parse", "--git-common-dir"], project, timeout=30)
    common = (r.stdout or "").strip() if r.returncode == 0 else ""
    if not common:
        return project
    p = Path(common)
    if not p.is_absolute():
        p = (project / p).resolve()
    return p.parent if p.name == ".git" else project


def _origin_tree(project: Path, branch: str) -> tuple[Path | None, str]:
    """A detached worktree sitting exactly on origin/<branch> — where the plan
    commit is BUILT. Returns (path, why-not); best-effort like everything here.
    """
    wt = _primary_worktree(project) / PUBLISH_TREE
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
        "  docs/goals.html — the plan as one self-contained page, served by\n"
        "                    GitHub Pages at the repo's site\n\n"
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
    # Current on origin but never read back (a lagging Pages build, or the first
    # publish of this file): check it here rather than pushing again.
    if _why.startswith("the mirror is on origin"):
        return _result(project, True, "", "already on origin")

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
        # Retire the files this mirror replaced, in the same commit: a stale
        # plan.md next to a current goals.html is two plans, one of them a lie.
        gone = [f for f in RETIRED if (wt / f).exists()]
        if gone:
            _g(["rm", "-q", "--", *gone], wt, timeout=60)
        paths = list(FILES) + gone
        _g(["add", "--", *FILES], wt, timeout=60)
        if _g(["diff", "--cached", "--quiet", "--", *paths], wt, timeout=60).returncode == 0:
            # origin already carries exactly this plan (a run published it first).
            return _result(project, True, "", "already on origin")
        c = _g(["commit", "-m", note, "--", *paths], wt, timeout=120)
        if c.returncode != 0:
            tail = (c.stderr or c.stdout or "").strip().splitlines()
            return {"pushed": False, "url": None, "sha": "",
                    "why": f"the plan commit failed: {tail[-1] if tail else 'unknown'}"}
        sha = (_g(["rev-parse", "--short", "HEAD"], wt, timeout=30).stdout or "").strip()
        r = _g(["push", "origin", f"HEAD:{branch}"], wt, timeout=180)
        if r.returncode == 0:
            return _result(project, True, sha, "published")
        if log:
            log("push rejected — origin moved; rebuilding the plan commit on the new tip")
    return {"pushed": False, "url": None, "sha": "",
            "why": "the push was rejected 3 times — the plan commit was not needed "
                   "on origin; the next run retries"}


def fingerprint(path: Path) -> str:
    """A byte fingerprint of one file or a whole tree ('' when absent).

    Commands must be able to tell THEIR OWN writes from what was already on disk:
    a checkout that is behind origin carries old bytes in a file the command never
    touched, and copying those onto main is a silent revert.
    """
    if path.is_dir():
        h = hashlib.sha1()
        for f in sorted(p for p in path.rglob("*") if p.is_file()):
            h.update(str(f.relative_to(path)).encode())
            h.update(b"\0")
            h.update(f.read_bytes())
            h.update(b"\0")
        return h.hexdigest()
    if path.is_file():
        return hashlib.sha1(path.read_bytes()).hexdigest()
    return ""


def snapshot_paths(project: Path, paths: list[str]) -> dict[str, str]:
    """Take this BEFORE the command writes anything: `commit_paths(baseline=…)`
    then commits only the paths whose bytes changed under the command."""
    return {str(p).strip(): fingerprint(project / str(p).strip())
            for p in paths if str(p).strip()}


def commit_paths(project: Path, paths: list[str], message: str, log=None,
                 baseline: dict[str, str] | None = None) -> dict:
    """Commit the LOCAL versions of `paths` onto origin/<main>, from the anchored
    worktree. Generic form of `publish()` for any command that writes into the
    project directory (store assets, harness scaffolding, listing edits).

    The checkout is never staged, rebased or reset: the identical bytes are
    copied into a detached worktree sitting on origin, committed there and
    pushed. So a dirty or diverged checkout cannot block the commit, and the
    owner's own WIP is never swept into it — callers pass only the paths their
    command touched.

    `baseline` (from `snapshot_paths`, taken before the command ran) narrows that
    to what the command actually WROTE. Without it a stale checkout publishes its
    old copies of every path in the list: on 2026-11-02 a `storefront-scenes
    --render` run, which touched no spec at all, committed the checkout's
    five-commits-behind docs/storefront.json back onto main and reverted a fix
    that had shipped minutes earlier.

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
    if baseline is not None:
        untouched = [rel for rel in keep
                     if rel in baseline and fingerprint(project / rel) == baseline[rel]]
        if untouched:
            say(f"not committing {len(untouched)} path(s) this command did not write: "
                + ", ".join(untouched))
            keep = [rel for rel in keep if rel not in untouched]
        if not keep:
            return {"pushed": True, "sha": "",
                    "why": "this command changed none of these paths"}
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
        # A path that exists in NEITHER the checkout nor origin has nothing to
        # stage, and `git add` treats it as a fatal pathspec error ("did not match
        # any files") — it then adds NOTHING, the index still matches HEAD and this
        # function reported success while the caller's deltas stayed in the
        # checkout only. Callers legitimately pass aspirational paths (a frames
        # dir no run has created yet), so drop the missing ones instead of failing.
        addable = [rel for rel in keep if (wt / rel).exists()]
        if addable:
            a = _g(["add", "-A", "--", *addable], wt, timeout=120)
            if a.returncode != 0:
                tail = (a.stderr or a.stdout or "").strip().splitlines()
                return {"pushed": False, "sha": "",
                        "why": f"git add failed: {tail[0] if tail else 'unknown'}"}
        if _g(["diff", "--cached", "--quiet", "--", *keep], wt, timeout=60).returncode == 0:
            say("nothing to commit — origin already carries these bytes")
            return {"pushed": True, "sha": "", "why": "already on origin"}
        c = _g(["commit", "-m", message, "--", *(addable or keep)], wt, timeout=120)
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
    """One owner-facing line about the shared plan.

    A push and a served page are different facts: the page is claimed only when
    it was read back, and a push whose build has not landed says so instead of
    borrowing the failure wording.
    """
    if res.get("pushed") and res.get("url"):
        return f"shared plan: {res['url']}   ({res.get('why') or 'published'})"
    if res.get("pushed"):
        return f"shared plan: pushed, not live yet — {res.get('why') or 'unknown reason'}"
    return f"shared plan: NOT pushed — {res.get('why') or 'unknown reason'}"
