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

The file is optional. A project with no `PRODUCT.md` is not blocked; declaring
the law is a choice, and once declared the team holds you to it.
"""
from __future__ import annotations

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
TEXT_EXT = {".py", ".kt", ".kts", ".sql", ".md", ".ts", ".tsx", ".js", ".swift"}

_CLAUSE_BULLET = re.compile(
    r"^\s*(?:[-*+]\s+)?\*{0,2}(P[1-9]\d{0,2})\*{0,2}\s*[.)—:–-]?\s+(.+?)\s*$"
)
_CLAUSE_TABLE = re.compile(
    r"^\s*\|\s*\*{0,2}(P[1-9]\d{0,2})\*{0,2}\s*\|\s*(.+?)\s*\|"
)
#: Clause ids start at P1 — `P0` is a priority word, never a clause.
CITATION = re.compile(r"\b(P[1-9]\d{0,2})\b")


def path_of(project: Path) -> Path | None:
    """The project's law file, or None when the law is not declared."""
    for name in CANDIDATES:
        candidate = Path(project) / name
        if candidate.is_file():
            return candidate
    return None


def clauses(project: Path) -> list[dict[str, str]]:
    """Every clause, in file order. `[{id, text}]` — an empty list means no law."""
    path = path_of(project)
    if path is None:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = _CLAUSE_TABLE.match(raw) or _CLAUSE_BULLET.match(raw)
        if not match:
            continue
        clause_id, text = match.group(1).upper(), match.group(2).strip()
        if clause_id in seen or not text:
            continue
        seen.add(clause_id)
        out.append({"id": clause_id, "text": text.rstrip("|").strip()})
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


def audit(project: Path, state: dict | None = None) -> dict[str, Any]:
    """One checkable answer: is the declared law alive, cited, and obeyed?

    `state` is the project ledger, when the caller has it; queued tasks are then
    checked for the clause they claim to serve.
    """
    from .core import state_of

    declared = clauses(project)
    present = bool(path_of(project))
    cited = citations(project) if present else {}
    ids = {clause["id"] for clause in declared}
    orphans = [c["id"] for c in declared if not cited.get(c["id"])]
    unknown = sorted(cid for cid in cited if cid not in ids)

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
        "tasks_without_law": tasks_without_law,
    }


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
        lines.append(f"  {mark}{clause['id']}  {clause['text'][:80]}")
        lines.append(f"        {first}" + (f"  (+{len(where) - 1})" if len(where) > 1 else ""))
    if result["unknown"]:
        lines.append("  !! cited but not declared: " + ", ".join(result["unknown"]))
    if result["tasks_without_law"]:
        lines.append("  !! queued with no clause: " + ", ".join(result["tasks_without_law"]))
    return "\n".join(lines)
