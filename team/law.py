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

DECLARING the law is mandatory (owner decision 2026-11-22), and it is enforced
at the door rather than in a comment: `admit` refuses a request for a project
that has neither a `PRODUCT.md` nor a recorded owner waiver, `pick` refuses to
hand a clause-less task to a run, and the planner may not queue work that amends
or contradicts a clause — an amendment is the owner's act (`devteam law
<project> --amend`). The one exception is written down, not assumed: an owner
may waive the law in their own words (`--waive --requested-by "<owner words>"`),
and the waiver lives in `profile.json` where `doctor` can read it back.
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


#: The profile key that records the owner's decision to run without a law.
WAIVER_KEY = "law_waiver"


def waiver(profile: dict | None) -> dict[str, Any]:
    """The owner's recorded decision not to declare a law — `{}` when none.

    A waiver is not silence: it carries the owner's own words and the date, so
    `doctor` and `status` can say WHO decided the product has no written law
    instead of pretending the file was forgotten.
    """
    block = (profile or {}).get(WAIVER_KEY) or {}
    if not isinstance(block, dict) or not block.get("waived"):
        return {}
    return block


def _profile_or_empty(project: Path) -> dict:
    """The project profile when there is one — `{}` for a bare directory.

    `law` is used on plain folders by tests and by `doctor` on a project whose
    profile is still unreadable; neither may raise here.
    """
    try:
        from .core import profile_of

        return profile_of(Path(project)) or {}
    except (Exception, SystemExit):
        return {}


def readiness(project: Path, profile: dict | None = None) -> tuple[str, str]:
    """Is the product law DECIDED? `("ready"|"blocked", one actionable line)`.

    Declaring the law is mandatory (owner decision 2026-11-22); the only other
    legal state is an owner waiver in their own words. Anything else is a gap
    the next run must not paper over, so the message names both fixes.
    """
    if path_of(project) is not None:
        return "ready", ""
    block = waiver(profile)
    if block:
        who = str(block.get("requested_by") or "").strip()
        at = str(block.get("at") or "")[:10]
        return "ready", ("law not declared — the owner waived it"
                         + (f' ("{who[:80]}")' if who else "")
                         + (f" on {at}" if at else ""))
    return "blocked", (
        "no PRODUCT.md — the product law is not declared. Write it with the "
        "owner (one clause per line, ids P1…, each with an indented detail "
        "block when the sentence needs a screen-level spec), or record their "
        'decision to run without one: devteam law <project> --waive '
        '--requested-by "<owner words>"'
    )


def entry_block(project: Path, entry: dict, profile: dict | None = None) -> str:
    """Why a queued ENTRY may not be built — `""` when it may.

    `doctor` already reported a clause-less queue as a warning. It is a refusal
    at the moment that matters (owner decision 2026-11-22: "what gets built
    always aligns with the product rule"): a run that picks work nobody can tie
    to a clause is a run with no reason in the product. `release`/`store` are
    exempt — they deliver work that named its clause when it was admitted.
    """
    status, _ = readiness(project, profile)
    if status != "ready" or path_of(project) is None:
        return ""
    if _exempt_entry(entry):
        return ""
    if str(entry.get("law") or "").strip():
        return ""
    label = str(entry.get("label") or entry.get("id") or "?")
    return (f"'{label}' names no product clause — tie it to the one it serves "
            f"(`devteam law {Path(project).name} --cite '{label}' --law <Pn> "
            '--why "<why it serves it>"`), re-admit it, or drop it; a run may not '
            "build work the product cannot justify")


def _exempt_entry(entry: dict) -> bool:
    """Entries that deliver or re-skin work already admitted under a clause.

    `release`/`store` were admitted as the delivery half of a task; an
    `icon-refresh` task is the owner's own sentence about the app's mark, born
    from `brand` with its provenance in the commit. None of them introduces a
    new product behaviour, which is what a clause decides.
    """
    kind = str(entry.get("kind") or entry.get("type") or "").strip().lower()
    if kind in ("release", "store"):
        return True
    return str(entry.get("label") or "").startswith("icon-refresh")


def write_block(project: Path, entry: dict, profile: dict | None = None) -> str:
    """Why this entry may not ENTER the queue — `""` when it may.

    Stronger than `entry_block`, and the reason it exists (owner delegation
    2026-11-22: no new development without a product law): an app that has not
    DECIDED what it is accepts no new queued work at all, not even work with a
    plausible clause. The law is the door, not a coat check.
    """
    status, note = readiness(project, _profile_or_empty(project) if profile is None else profile)
    if status != "ready":
        return note
    return entry_block(project, entry, profile)


def _indent_of(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" \t"))


def _detail_span(lines: list[str], index: int) -> tuple[int, int]:
    """Where a clause's DETAIL block lives: `(start, end)` half-open, after `index`.

    The detail is the indented prose under a clause — the part that says what the
    sentence MEANS on a screen (owner decision 2026-11-22: "this needs more
    detailed writeup in product, not just your small point"). Indentation is the
    only marker: the sentence stays quotable and citable, and the block under it
    is read by `devteam law --show` and by anyone implementing the clause.
    """
    indent = _indent_of(lines[index])
    start = index + 1
    end = start
    while end < len(lines):
        raw = lines[end]
        if not raw.strip():
            end += 1
            continue
        if _indent_of(raw) <= indent:
            break
        if _CLAUSE_BULLET.match(raw) or _CLAUSE_TABLE.match(raw):
            break
        end += 1
    return start, end


def _detail_text(lines: list[str], index: int) -> str:
    """The detail block as written, with its shared indentation removed."""
    start, end = _detail_span(lines, index)
    block = lines[start:end]
    while block and not block[-1].strip():
        block.pop()
    while block and not block[0].strip():
        block.pop(0)
    if not block:
        return ""
    indents = [_indent_of(line) for line in block if line.strip()]
    cut = min(indents) if indents else 0
    return "\n".join(
        line[cut:].rstrip() if line.strip() else "" for line in block
    ).strip()


def _detail_lines(text: str, indent: int) -> list[str]:
    """A detail block rendered under a clause: a blank line, then indented prose."""
    body = str(text or "").strip()
    if not body:
        return []
    pad = " " * (indent + 2)
    return [""] + [pad + line if line.strip() else "" for line in body.splitlines()]


def clauses(project: Path) -> list[dict[str, Any]]:
    """Every clause, in file order. `[{id, text, binds, detail}]` — empty = no law.

    `text` is the one-sentence law (the quotable, citable half). `detail` is the
    indented block under it, or `""` — the sentence is what a report quotes, the
    detail is what an implementation follows.
    """
    path = path_of(project)
    if path is None:
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    for index, raw in enumerate(lines):
        match = _CLAUSE_TABLE.match(raw) or _CLAUSE_BULLET.match(raw)
        if not match:
            continue
        clause_id, text = match.group(1).upper(), match.group(2).strip()
        if clause_id in seen or not text:
            continue
        seen.add(clause_id)
        binds, spoken = _split_binds(text.rstrip("|").strip())
        out.append({"id": clause_id, "text": spoken, "binds": binds,
                    "detail": _detail_text(lines, index)})
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


def bind_surfaces(mapping: dict[str, list[str]],
                  pairs: list[tuple[str, str]]) -> tuple[dict[str, list[str]], list[str], str | None]:
    """Name law surfaces — `[(name, glob)]` into `{name: [glob, …]}`.

    Mutating a project's `law_bindings` is how a project says what it publishes,
    so the two verbs that do it (`--bind`, `--unbind`) share these pure helpers:
    a wrong name must be undoable by the verb that wrote it, and the CLI only
    prints and saves what this returns.
    """
    named = {str(k): list(v) for k, v in (mapping or {}).items()}
    changed: list[str] = []
    for raw_name, raw_glob in pairs:
        name = str(raw_name).strip().lower()
        glob = str(raw_glob).strip()
        if not name or not glob:
            return named, changed, "--bind needs a surface name and a glob: --bind site 'docs/**'"
        globs = named.setdefault(name, [])
        if glob not in globs:
            globs.append(glob)
            changed.append(f"{name} ← {glob}")
    return named, changed, None


def unbind_surfaces(mapping: dict[str, list[str]],
                    items: list[list[str]]) -> tuple[dict[str, list[str]], list[str], str | None]:
    """Drop one glob from a surface, or the whole surface when no glob follows."""
    named = {str(k): list(v) for k, v in (mapping or {}).items()}
    dropped: list[str] = []
    for item in items:
        parts = list(item)
        name, glob = (parts + [None])[:2]
        name = str(name).strip().lower()
        if name not in named:
            return named, dropped, f"no surface named '{name}' to unbind"
        if glob:
            glob = str(glob).strip()
            globs = [g for g in named[name] if g != glob]
            if len(globs) == len(named[name]):
                return named, dropped, f"'{name}' does not carry {glob}"
            if globs:
                named[name] = globs
            else:
                named.pop(name, None)
        else:
            named.pop(name, None)
        dropped.append(name + (f" ← {glob}" if glob else " (whole surface)"))
    return named, dropped, None


def amend(project: Path, clause_id: str, text: str, requested_by: str,
          detail: str | None = None) -> tuple[bool, str]:
    """Rewrite ONE clause's sentence (and optionally its detail) — the owner's act.

    An amendment needs the owner's own words on the record (the same rule
    `promote` follows): code cannot invent a new product, it can only write down
    what the owner said. The clause id and any `binds:` marker it already
    carried are preserved unless the new sentence declares its own — a
    tightening of the wording must not silently drop a platform from the gate.

    `detail=None` keeps the block under the clause exactly as the owner wrote it;
    passing a string replaces it. A new sentence that describes behaviour no one
    has written down yet is a half-amendment, so the caller is expected to pass
    one — `--detail ""` clears it deliberately.
    """
    cid = str(clause_id or "").strip().upper()
    new_text = " ".join(str(text or "").split())
    who = " ".join(str(requested_by or "").split())
    if not cid:
        return False, "say which clause: --amend P7"
    if not new_text:
        return False, f'say the new sentence for {cid}: --text "<one clause>"'
    if not who:
        return False, ('an amendment is the owner\'s act — quote them: '
                       '--requested-by "<the owner\'s words>"')
    path = path_of(Path(project))
    if path is None:
        return False, "no PRODUCT.md — there is no clause to amend (declare the law first)"
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, raw in enumerate(lines):
        match = _CLAUSE_TABLE.match(raw) or _CLAUSE_BULLET.match(raw)
        if not match or match.group(1).upper() != cid:
            continue
        old_binds, _ = _split_binds(match.group(2).rstrip("|").strip())
        new_binds, spoken = _split_binds(new_text)
        binds = new_binds or old_binds
        marker = (" (binds: " + ", ".join(binds) + ")") if binds else ""
        old_detail = _detail_text(lines, index)
        body = old_detail if detail is None else str(detail).strip()
        lines[index] = f"- **{cid}** — {spoken}{marker}"
        start, end = _detail_span(lines, index)
        block = _detail_lines(body, 0)
        lines[start:end] = block + ([""] if block else [])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        changed = "sentence" if detail is None else "sentence and detail"
        return True, f"{cid} — {spoken}{marker} ({changed})"
    return False, f"{cid} is not a clause in {path.name} (nothing was changed)"


def add(project: Path, clause_id: str, text: str, requested_by: str,
        detail: str = "", binds: list[str] | None = None) -> tuple[bool, str]:
    """DECLARE one new clause, with its detail — the owner's act.

    Why a verb and not an editor (2026-11-22): a clause is what every later run is
    judged against, and hand-editing the file was the way a clause got added
    without its owner's words anywhere on the record. `--add` writes the same
    shape `--amend` preserves — one sentence, then an indented detail block — so
    the law can be as precise as the screen it must produce.
    """
    cid = str(clause_id or "").strip().upper()
    new_text = " ".join(str(text or "").split())
    who = " ".join(str(requested_by or "").split())
    if not re.fullmatch(r"P[1-9]\d{0,2}", cid):
        return False, 'say the clause id: --add P16 (P1, P2, … — P0 is a priority word)'
    if not new_text:
        return False, f'say the one sentence for {cid}: --text "<one clause>"'
    if not who:
        return False, ('declaring a clause is the owner\'s act — quote them: '
                       '--requested-by "<the owner\'s words>"')
    path = path_of(Path(project))
    if path is None:
        path = Path(project) / "PRODUCT.md"
        path.write_text("# PRODUCT — the law this app renders\n\n", encoding="utf-8")
    lines = path.read_text(encoding="utf-8").splitlines()
    declared = {clause["id"]: clause for clause in clauses(Path(project))}
    if cid in declared:
        return False, (f"{cid} is already declared — change it with --amend {cid} "
                       "--text \"…\" --detail \"…\" (nothing was changed)")
    spoken, marker = new_text, ""
    if binds:
        marker = " (binds: " + ", ".join(b.strip().lower() for b in binds if b.strip()) + ")"
    # Place it after the last clause and its detail, so the file stays one list.
    insert_at = len(lines)
    for index, raw in enumerate(lines):
        match = _CLAUSE_TABLE.match(raw) or _CLAUSE_BULLET.match(raw)
        if match:
            insert_at = _detail_span(lines, index)[1]
    while insert_at > 0 and not lines[insert_at - 1].strip():
        insert_at -= 1
    block = [f"- **{cid}** — {spoken}{marker}"] + _detail_lines(detail, 0)
    lines[insert_at:insert_at] = ([""] if insert_at else []) + block
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tail = " + detail" if str(detail or "").strip() else ""
    return True, f"{cid} — {spoken}{marker}{tail} (declared)"


def show(project: Path, clause_id: str) -> tuple[bool, str]:
    """One clause as written: the sentence, its detail, and who cites it."""
    cid = str(clause_id or "").strip().upper()
    for clause in clauses(Path(project)):
        if clause["id"] != cid:
            continue
        binds = ("  (binds " + ", ".join(clause["binds"]) + ")") if clause.get("binds") else ""
        lines = [f"{clause['id']} — {clause['text']}{binds}"]
        if clause.get("detail"):
            lines.append("")
            lines += ["  " + line if line.strip() else "" for line in clause["detail"].splitlines()]
        where = citations(Path(project)).get(cid) or []
        lines.append("")
        lines.append(f"cited: {where[0]}" if where else
                     "cited: nothing yet — a clause nothing cites is dead law")
        return True, "\n".join(lines)
    known = ", ".join(c["id"] for c in clauses(Path(project))) or "none"
    return False, f"{cid or '?'} is not in PRODUCT.md (known: {known})"


def prompt_block(project: Path, profile: dict | None = None) -> str:
    """THE LAW, as the planner reads it — a constraint, not a suggestion.

    The plan prompt used to carry the vision, the goals and the queue and say
    nothing about what the product IS, so a feature could be born contradicting
    a clause and no gate saw it (the plan's queue had no `law` field at all).
    This block is what `_validate_law` holds the answer to.
    """
    declared = clauses(Path(project))
    if not declared:
        status, _note = readiness(Path(project), profile)
        if status == "ready":
            return ("THE PRODUCT LAW: this project declares no PRODUCT.md (the "
                    "owner waived it). Queue entries need no clause; do not "
                    "invent one, and keep every entry consistent with the vision.")
        return ("THE PRODUCT LAW: this project has not declared one. Do not "
                "invent clauses as if they were law — if a clause is needed, "
                "propose it in issues_to_file and let the owner declare it.")
    lines = ["THE PRODUCT LAW (PRODUCT.md) — WHAT the product is. A plan may not "
             "contradict it; the goal decides how far it goes, the law decides "
             "what it may never become:"]
    for clause in declared:
        binds = ("  (binds " + ", ".join(clause["binds"]) + ")") if clause.get("binds") else ""
        detail = "  +detail" if clause.get("detail") else ""
        lines.append(f"  {clause['id']} — {clause['text']}{binds}{detail}")
    lines.append("")
    lines.append("- the line above is the quotable law; a clause marked `+detail` "
                 'carries a written spec under it in PRODUCT.md — read it with '
                 f"`devteam law <project> --show {declared[0]['id']}` before you "
                 "plan work that touches it. The detail is binding: it says what "
                 "the sentence means on a screen.")
    lines.append('- every queue entry carries "law": ["<Pn>", …] — the clause(s) '
                 "it SERVES. An entry with no clause, or with an id not in the "
                 "list above, is rejected.")
    lines.append('- every queue entry carries "law_effect": "obeys". If the right '
                 "fix needs a clause to change, do NOT queue it: file it in "
                 "issues_to_file naming the clause and the sentence you propose. "
                 "Changing the law is the owner's act (`devteam law <project> "
                 "--amend`), never the planner's.")
    lines.append('- a new milestone may carry "law": [ids] — the clauses it exists '
                 "to render, inherited by the entries queued under it.")
    return "\n".join(lines)


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
            if str(task.get("kind") or task.get("type") or "") in ("release", "store"):
                continue
            if not str(task.get("law") or "").strip():
                tasks_without_law.append(str(task.get("label") or task.get("id") or "?"))

    profile = _profile_or_empty(project)
    status, note = readiness(project, profile)
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
        # The law is mandatory: `doctor` reads these to say whether the product
        # has DECIDED what it is, or the owner waived the decision.
        "waived": bool(waiver(profile)),
        "status": status,
        "note": note,
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


def cite(project: Path, label: str, law: str, why: str,
         effect: str = "obeys") -> tuple[bool, str]:
    """Tie an ALREADY-QUEUED entry to the clause it serves — the agent's act.

    WHY THIS EXISTS (2026-11-22). The planner used to queue work before the law
    existed on disk, so a project could inherit clause-less entries the moment the
    gate turned on. The refusal named "re-admit it", and `admit` builds a NEW
    entry from the owner's sentence — which left the old planner entry in the
    queue, still clause-less, still refusing `pick` when its turn came. So the
    classification of work that is already queued gets its own verb, and it is
    deliberately narrow: it changes no work and adds no work, it refuses a label
    that is not in the queue, it needs a reason, and it accepts only `obeys` —
    an amendment or a contradiction is a new request, which must go through
    `admit` so the owner sees the clause before anything is built.
    """
    from .core import now_iso, save_state, state_of

    label = " ".join(str(label or "").split())
    ids = [part.strip().upper() for part in (law or "").split(",") if part.strip()]
    why = " ".join(str(why or "").split())
    effect = str(effect or "obeys").strip().lower()
    if not label:
        return False, "say which queued entry: --cite <label>"
    if not ids:
        return False, "say which clause it serves: --cite <label> --law P4"
    if not why:
        return False, ('say why it serves that clause: --why "<one line>" — the '
                       "citation is recorded with the task")
    if effect != "obeys":
        return False, ("--cite only records work that SERVES a clause. An "
                       f"{effect} is a new request: run `devteam admit … --law "
                       f"{ids[0]} --effect {effect}` so the owner sees the clause "
                       "before anything is built")
    known = {clause["id"]: clause["text"] for clause in clauses(Path(project))}
    if not known:
        return False, "no PRODUCT.md — there is no clause to cite (declare the law first)"
    unknown = [cid for cid in ids if cid not in known]
    if unknown:
        return False, (f"{', '.join(unknown)} is not in PRODUCT.md (known: "
                       f"{', '.join(sorted(known))})")

    state = state_of(Path(project))
    queue = [e for e in (state.get("queue") or []) if isinstance(e, dict)]
    exact = [e for e in queue
             if str(e.get("label") or e.get("id") or "") == label]
    matches = exact or [e for e in queue
                        if label.lower() in str(e.get("label") or e.get("id") or "").lower()]
    if not matches:
        return False, (f"'{label}' is not queued (nothing was changed). Queued: "
                       + (", ".join(str(e.get("label") or "?") for e in queue) or "none"))
    if len(matches) > 1:
        return False, (f"'{label}' matches {len(matches)} entries — name one: "
                       + ", ".join(str(e.get("label") or "?") for e in matches))
    entry = matches[0]
    was = str(entry.get("law") or "").strip()
    if was and [part.strip().upper() for part in was.split(",")] == ids:
        return False, f"'{label}' already cites {', '.join(ids)} (nothing was changed)"
    entry["law"] = ",".join(ids)
    entry["law_effect"] = "obeys"
    entry["why"] = why
    entry["decided_by"] = "agent"
    state.setdefault("law_citations", []).append({
        "at": now_iso(), "label": str(entry.get("label") or label),
        "law": ",".join(ids), "effect": "obeys", "why": why, "was": was,
    })
    save_state(Path(project), state)
    return True, f"{entry.get('label') or label} → {', '.join(ids)} (obeys)"


def format_audit(result: dict[str, Any]) -> str:
    lines = ["LAW — the product's clauses and their citations", "=" * 66]
    if not result.get("present"):
        if result.get("waived"):
            lines.append("  no PRODUCT.md — "
                         + str(result.get("note") or "").replace(
                             "law not declared — ", ""))
        else:
            lines.append("  !! NO PRODUCT.md — the product law is not declared.")
            lines.append("     " + str(result.get("note") or
                                        "declare it, or record the owner's waiver"))
        return "\n".join(lines)
    lines.append(f"  file: {result['path']}")
    for clause in result["clauses"]:
        where = result["cited"].get(clause["id"]) or []
        mark = "ok " if where else "!! "
        first = where[0] if where else "nothing cites this clause"
        binds = ""
        if clause.get("binds"):
            binds = "  (binds " + ", ".join(clause["binds"]) + ")"
        detail = "  +detail" if clause.get("detail") else ""
        lines.append(f"  {mark}{clause['id']}  {clause['text'][:80]}{binds}{detail}")
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
