"""THE LAW — the product's own clauses, and who cites them.

Why this exists (2026-11-15, owner directive)
---------------------------------------------
The same product law lived in four places and was checked in none: the planner's
`PROMPT_HEADER`, the migration headers that record the owner's decisions, the
app's reader-facing copy and tests, and `docs/*.md`. One day of work found three
live drifts — a contract page still describing a deleted RPC, a tool page still
listing a deleted tool, and the model's instructions still saying "a two-way
match is BARE" hours after the match was removed.

So the law becomes ONE file per project, `PRODUCT.md`, with stable clause ids
(`P1`…). Everything else CITES it: a migration header names the clauses it
implements, a test is named after one, and `devteam admit --law P1` records which
clause an owner request serves.

`devteam doctor` then refuses three things:

  * a clause nothing cites        — dead law, or a clause the product forgot;
  * a citation of a missing id    — fiction, the classic stale-doc drift;
  * a queued task with no clause  — work with no reason in the product.

And `devteam verify` refuses a fourth, which is the one a review cannot see
(2026-11-20): a diff that prints a clause's law on a surface which does not
cite it. The project's public site had been generated from a table the law had
retired two days earlier — `P13` says a place is never named by a list of cities
— and every gate stayed green: `docs/*.md` cited the clause (prose DESCRIBES the
law, it does not OBEY it), the browser client cited it, and the marketing pages
that contradicted it were generated output no gate ever opened. So:

  * a citation in prose covers nothing but prose;
  * a `binds:` surface is the files that RENDER the law, and a diff that opens
    one must leave it citing the clauses it binds (`diff_gate`).

The file is optional. A project with no `PRODUCT.md` is not blocked; declaring
the law is a choice, and once declared the team holds you to it.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

#: Where a project may keep its law. Root first: the file is the front door.
CANDIDATES = ("PRODUCT.md", "docs/PRODUCT.md", "product/PRODUCT.md")

#: Directories that never hold a citation (build output, deps, machine state).
SKIP_DIRS = {
    ".git", ".pipeline", "build", "node_modules", "venv", ".venv", ".gradle",
    ".kotlin", ".idea", "__pycache__", "dist", "out", "Pods", "vendor",
}

#: Extensions worth reading. A citation in a PNG is not a citation.
TEXT_EXT = {".py", ".kt", ".kts", ".sql", ".md", ".ts", ".tsx", ".js", ".swift",
            ".html"}

#: PROSE DESCRIBES THE LAW; IT DOES NOT OBEY IT. A page of documentation citing
#: `P13` is evidence that somebody once understood the clause, and for two days
#: it was the only thing standing behind BookSwap's public site while the site
#: shipped the opposite. So prose counts for a binding only when the binding IS
#: prose — never as coverage for the code or the pages that render it.
PROSE_EXT = {".md"}

_CLAUSE_BULLET = re.compile(
    r"^\s*(?:[-*+]\s+)?\*{0,2}(P[1-9]\d{0,2})\*{0,2}\s*[.)—:–-]?\s+(.+?)\s*$"
)
_CLAUSE_TABLE = re.compile(
    r"^\s*\|\s*\*{0,2}(P[1-9]\d{0,2})\*{0,2}\s*\|\s*(.+?)\s*\|"
)
#: Clause ids start at P1 — `P0` is a priority word, never a clause.
CITATION = re.compile(r"\b(P[1-9]\d{0,2})\b")

#: A clause may say WHO it binds: `… (binds: app, web, backend)`. The marker is
#: declared once in the clause and read by every platform's own check.
_BINDS = re.compile(r"[\(\[]\s*binds?\s*:\s*([^)\]]+?)\s*[\)\]]", re.IGNORECASE)


def _split_binds(text: str) -> tuple[list[str], str]:
    """`(binds: app, web)` → `(["app", "web"], text without the marker)`.

    The marker is law, not prose: it leaves the sentence so `devteam law` prints
    one readable line per clause, and it names the platforms the clause is
    checked against (see `audit`).
    """
    match = _BINDS.search(text)
    if match is None:
        return [], text
    binds = [part.strip().lower() for part in re.split(r"[,\s]+", match.group(1))
             if part.strip()]
    return binds, (text[:match.start()] + text[match.end():]).strip()


def path_of(project: Path) -> Path | None:
    """The project's law file, or None when the law is not declared."""
    for name in CANDIDATES:
        candidate = Path(project) / name
        if candidate.is_file():
            return candidate
    return None


def clauses(project: Path) -> list[dict[str, Any]]:
    """Every clause, in file order. `[{id, text, binds}]` — empty means no law."""
    path = path_of(project)
    if path is None:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = _CLAUSE_TABLE.match(raw) or _CLAUSE_BULLET.match(raw)
        if not match:
            continue
        clause_id, text = match.group(1).upper(), match.group(2).strip()
        if clause_id in seen or not text:
            continue
        seen.add(clause_id)
        binds, spoken = _split_binds(text.rstrip("|").strip())
        out.append({"id": clause_id, "text": spoken, "binds": binds})
    return out


def citations(project: Path) -> dict[str, list[str]]:
    """Where each clause id is cited: `{"P1": ["path:12", ...]}`.

    The law file itself does not count — a clause citing itself is not a check.
    """
    project = Path(project)
    law_file = path_of(project)
    found: dict[str, list[str]] = {}
    for path in project.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXT:
            continue
        if law_file is not None and path.resolve() == law_file.resolve():
            continue
        if SKIP_DIRS & set(path.relative_to(project).parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel = str(path.relative_to(project))
        for number, line in enumerate(lines, 1):
            for clause_id in CITATION.findall(line):
                found.setdefault(clause_id.upper(), [])
                where = f"{rel}:{number}"
                if where not in found[clause_id.upper()]:
                    found[clause_id.upper()].append(where)
    return found


def _coverage(where: str, globs: list[str]) -> str:
    """Why a citation does NOT cover a binding — `""` when it does.

    Three reasons, and they are not the same failure: `outside` means the citing
    file is not on the surface the clause binds; `prose` means it is, but it is a
    document talking about the law rather than the thing that renders it.
    """
    place = where.rsplit(":", 1)[0]
    if not any(fnmatch.fnmatch(place, pattern) for pattern in globs):
        return "outside"
    if Path(place).suffix.lower() in PROSE_EXT:
        return "prose"
    return ""


def _tracked(project: Path) -> list[str]:
    """Every tracked path — a page tree is what git carries, not what is on disk."""
    from .core import git

    res = git(["ls-files"], Path(project), timeout=120)
    if res.returncode == 0:
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    return [str(p.relative_to(project)) for p in Path(project).rglob("*")
            if p.is_file() and not (SKIP_DIRS & set(p.relative_to(project).parts))]


#: A published page tree starts at an `index.html`.
SITE_SEEDS = {"index.html", "index.htm"}


def page_trees(project: Path, extra: list[str] | None = None) -> list[tuple[str, list[str]]]:
    """Published page trees: `[(root, files under it)]`.

    WHY THIS IS LAW AND NOT PLATFORM BOOKKEEPING (2026-11-20): the project's
    public site was a page tree generated from a table the law had retired, and
    nothing in the team could see it — `verify` built the app and the browser
    client, `doctor` counted the clauses those two cited, and the pages a reader
    actually opens were generated output outside every check. A page tree is a
    SURFACE: it prints the law to people who never open the app, so a project that
    declares clauses must name the tree in `law_bindings`. An unnamed tree is a
    finding here rather than a warning nobody reads, because the copy it prints is
    the product's most public claim.
    """
    paths = _tracked(project)
    for path in (extra or []):
        if path not in paths:
            paths.append(path)
    seeds: list[str] = []
    for rel in paths:
        parts = rel.split("/")
        if parts[-1].lower() not in SITE_SEEDS or not 2 <= len(parts) <= 3:
            continue
        seeds.append("/".join(parts[:-1]))
    roots: list[str] = []
    for root in sorted(set(seeds), key=lambda r: (r.count("/"), r)):
        if any(root == seen or root.startswith(seen + "/") for seen in roots):
            continue
        roots.append(root)
    return [(root, [p for p in paths if p.startswith(root + "/")]) for root in roots]


def audit(project: Path, state: dict | None = None) -> dict[str, Any]:
    """One checkable answer: is the declared law alive, cited, and obeyed?

    `state` is the project ledger, when the caller has it; queued tasks are then
    checked for the clause they claim to serve.
    """
    from .core import profile_of, state_of

    declared = clauses(project)
    present = bool(path_of(project))
    cited = citations(project) if present else {}
    ids = {clause["id"] for clause in declared}
    orphans = [c["id"] for c in declared if not cited.get(c["id"])]
    unknown = sorted(cid for cid in cited if cid not in ids)

    # A CLAUSE MAY BIND SEVERAL PLATFORMS, and one citation from one client is not
    # coverage: BookSwap's location law was "cited" by the Kotlin seam on the same
    # disk where the browser client still shipped the old list. `binds:` turns
    # "remember every platform" into a file count — the project declares which
    # globs belong to which name (`profile.law_bindings`), and a binding with no
    # citation under its own globs is a finding, not a footnote.
    #
    # And the citation has to be an OBEDIENT one: a `.md` page saying "P13 says a
    # place is never named by a list of cities" was counted as `web` coverage for
    # two days while the published pages it describes did the opposite, so prose
    # no longer covers a binding that renders anything (`_coverage`).
    #
    # ONE SURFACE PER GLOB. A name may map to several globs, and coverage is
    # checked per glob: a citation in the browser client cannot vouch for the
    # pages served from `docs/`, because those are different things readers open.
    try:
        profile = profile_of(Path(project)) or {}
    except (Exception, SystemExit):
        profile = {}
    mapping = {
        str(name).strip().lower(): [str(g) for g in (globs or []) if str(g)]
        for name, globs in (profile.get("law_bindings") or {}).items()
    }
    unbound: list[dict[str, str]] = []
    for clause in declared:
        for binding in clause.get("binds") or []:
            globs = mapping.get(binding) or []
            if not globs:
                unbound.append({"clause": clause["id"], "binding": binding,
                                "reason": "unmapped", "glob": ""})
                continue
            for glob in globs:
                reasons = [_coverage(where, [glob])
                           for where in (cited.get(clause["id"]) or [])]
                if any(reason == "" for reason in reasons):
                    continue
                unbound.append({"clause": clause["id"], "binding": binding,
                                "glob": glob,
                                "reason": "prose" if "prose" in reasons else "uncited"})

    # A SURFACE NO CLAUSE KNOWS (2026-11-20): the page trees this project
    # publishes, and which of them no `law_bindings` glob names. Declaring the law
    # without naming the tree is how the site shipped copy the law forbids while
    # every binding still read "ok".
    every_glob = [glob for globs in mapping.values() for glob in globs]
    uncovered = [
        {"root": root, "pages": len(files)}
        for root, files in page_trees(project)
        if not any(fnmatch.fnmatch(path, glob) for path in files for glob in every_glob)
    ] if present else []

    tasks_without_law: list[str] = []
    if present:
        ledger = state if state is not None else state_of(Path(project))
        for task in (ledger.get("queue") or []):
            if not isinstance(task, dict):
                continue
            # `release`/`store` deliver work that was already admitted under a
            # clause; they do not need to name one again.
            if str(task.get("kind") or "") in ("release", "store"):
                continue
            if not str(task.get("law") or "").strip():
                tasks_without_law.append(str(task.get("label") or task.get("id") or "?"))

    return {
        "present": present,
        "path": str(path_of(project)) if present else "",
        "clauses": declared,
        "cited": cited,
        "orphans": orphans,
        "unknown": unknown,
        "unbound": unbound,
        "uncovered": uncovered,
        "tasks_without_law": tasks_without_law,
    }


def diff_gate(project: Path, worktree: Path, files: list[str]) -> tuple[bool, str]:
    """A diff that opens a bound surface leaves that surface citing its clauses.

    THE GAP THIS CLOSES (2026-11-20). `audit` asks "is each clause cited
    somewhere?" — a question no reviewer and no gate could answer wrongly about a
    surface that had quietly stopped obeying it. The published site was rebuilt
    from a table the law had retired, page copy said a city decides which books
    are near, and every check was green: the app cited `P13`, the browser client
    cited `P13`, and a `.md` in `docs/` cited `P13`, while the pages in `docs/`
    that a reader actually opens contradicted it.

    So the check is scoped to the DIFF: if a changed file lands under a
    `law_bindings` glob whose name some clause binds, then that clause must be
    cited by a non-prose file under those same globs in this worktree. Diff-scoped
    on purpose — a surface this change never opened is `doctor`'s warning, not a
    refusal of unrelated work (the same split `platforms.diff_gate` uses).
    """
    from .core import profile_of

    try:
        profile = profile_of(Path(project)) or {}
    except (Exception, SystemExit):
        profile = {}
    mapping = {
        str(name).strip().lower(): [str(g) for g in (globs or []) if str(g)]
        for name, globs in (profile.get("law_bindings") or {}).items()
    }
    if not mapping or not files:
        return True, ""
    law_file = path_of(Path(worktree)) or path_of(Path(project))
    if law_file is None:
        return True, ""
    declared = clauses(Path(worktree)) or clauses(Path(project))
    every_glob = [glob for globs in mapping.values() for glob in globs]
    # A page tree this diff publishes must be a surface the law knows about
    # BEFORE it is judged on what it says: an unnamed tree has no clause to cite.
    unnamed = [
        root for root, pages in page_trees(Path(worktree), extra=files)
        if any(path == root or path.startswith(root + "/") for path in files)
        and not any(fnmatch.fnmatch(path, glob) for path in pages for glob in every_glob)
    ]
    if unnamed:
        return False, (
            "A PUBLISHED SURFACE NO CLAUSE KNOWS: this diff writes "
            + ", ".join(unnamed) + " — a page tree readers open — and no "
            "law_bindings glob names it, so nothing checks the law it prints.\n"
            '  name it in profile.json next to the others: "law_bindings": '
            '{"<surface>": ["' + unnamed[0] + '/**"]}, and cite the clauses that '
            "surface renders from the file that generates it."
        )
    touched = {
        name for name, globs in mapping.items()
        if any(fnmatch.fnmatch(path, pattern) for path in files for pattern in globs)
    }
    if not touched:
        return True, ""
    cited = citations(Path(worktree)) if path_of(Path(worktree)) else citations(Path(project))

    gaps: list[str] = []
    for clause in declared:
        for binding in clause.get("binds") or []:
            if binding not in touched:
                continue
            for glob in (mapping.get(binding) or []):
                if not any(fnmatch.fnmatch(path, glob) for path in files):
                    continue
                if any(_coverage(where, [glob]) == ""
                       for where in (cited.get(clause["id"]) or [])):
                    continue
                gaps.append(f"  {clause['id']} binds {binding}, and this diff changes "
                            f"{glob} files, but nothing under {glob} cites "
                            f"{clause['id']} in a file that renders it")
    if not gaps:
        return True, ""

    names = ", ".join(sorted(touched))
    lines = [
        f"LAW NOT RENDERED BY THE SURFACE THAT PUBLISHED IT: this diff opens "
        f"{names}, and {len(gaps)} clause(s) bound to it are not cited there."
    ]
    lines += gaps
    lines.append(
        "  a citation in a .md describes the law; it does not obey it. Cite the "
        "clause in the file that RENDERS it — a header comment on the generator "
        "(e.g. `# Law: P3, P13`) or a constant named after it — so the page that "
        "prints the rule is the page that names it. If the surface does not "
        "actually obey the clause, fix the surface: that is what the gate is for."
    )
    return False, "\n".join(lines)


def format_audit(result: dict[str, Any]) -> str:
    lines = ["LAW — the product's clauses and their citations", "=" * 66]
    if not result.get("present"):
        lines.append("  no PRODUCT.md — the law is not declared for this project.")
        return "\n".join(lines)
    lines.append(f"  file: {result['path']}")
    for clause in result["clauses"]:
        where = result["cited"].get(clause["id"]) or []
        mark = "ok " if where else "!! "
        first = where[0] if where else "nothing cites this clause"
        binds = ""
        if clause.get("binds"):
            binds = "  (binds " + ", ".join(clause["binds"]) + ")"
        lines.append(f"  {mark}{clause['id']}  {clause['text'][:80]}{binds}")
        lines.append(f"        {first}" + (f"  (+{len(where) - 1})" if len(where) > 1 else ""))
    for gap in result.get("unbound") or []:
        reason = {
            "unmapped": "the project declares no law_bindings glob for it",
            "prose": "only prose under it cites the clause — a document describing "
                     "the law is not a surface obeying it",
            "uncited": "nothing under it cites the clause",
        }.get(gap["reason"], "nothing under its own globs cites it")
        where = f" ({gap['glob']})" if gap.get("glob") else ""
        lines.append(f"  !! {gap['clause']} binds {gap['binding']}{where} — {reason}")
    for gap in result.get("uncovered") or []:
        lines.append(f"  !! {gap['root']} is a published page tree ({gap['pages']} page(s)) "
                     "that no law_bindings glob names — the law a reader sees there "
                     "is checked nowhere")
    if result["unknown"]:
        lines.append("  !! cited but not declared: " + ", ".join(result["unknown"]))
    if result["tasks_without_law"]:
        lines.append("  !! queued with no clause: " + ", ".join(result["tasks_without_law"]))
    return "\n".join(lines)
