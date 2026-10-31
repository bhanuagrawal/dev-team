"""Requirement hygiene for the team's own procedure documents.

Why this exists
---------------
The procedure an agent follows lives in four places that different runs edit:
`AGENTS.md` (the contract), `skills/dev-team/SKILL.md` (how an agent works),
`README.md` (the owner-facing map) and `skills/lessons.md` (append-only history,
never rewritten). Nothing kept them agreeing. A rule could be added in one file
while an older, contradicting copy survived in another — and an agent following
the older copy silently skips a step.

That is not hypothetical. The store-asset workflow required the poster to be
composed by Stitch or the vision model (contract + lessons), while the step list
an agent actually executes said only "`--frame` writes 01_<slug>.png". Both were
in the repo, both were "documented", and a release shipped the builtin template.

The rule this module enforces
-----------------------------
**The newest text is the source of truth.** When two places state the same
requirement, the older copy is removed rather than kept alongside. The audit says
which copy is older by `git blame`, so "newest" means newest commit, not newest
wording.

What is detected (deterministic — no model, same answer every run)
-----------------------------------------------------------------
  duplicate-command  the same `devteam <cmd>` or `--<flag>` is documented in two
                     or more documents
  near-duplicate     two sentences say the same thing modulo wording (Jaccard)
  contradiction      two sentences share a subject and disagree on negation

Only the first two are auto-removable: a contradiction needs a decision about
which side is right, so it always blocks and is listed for the author.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Known procedure documents, in the order an agent is expected to read them.
# lessons.md is deliberately absent: it is append-only history, and deleting an
# old lesson is how a team forgets why a rule exists.
DOC_FILES = ("AGENTS.md", "skills/dev-team/SKILL.md", "README.md")

# A sentence has to be long enough that "same statement, different wording" is
# meaningful; short lines ("Run it.") collide by accident.
MIN_TOKENS = 6
NEAR_DUP = 0.8

NEGATIONS = {"never", "not", "no", "nothing", "cannot", "cant", "mustnt", "dont", "wont"}

STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "be",
    "it", "its", "that", "this", "with", "as", "at", "by", "from", "if", "then",
    "you", "your", "we", "our", "they", "them", "their", "so", "but", "not", "never",
    "must", "should", "can", "will", "do", "does", "has", "have", "was", "were",
}
# Negations are deliberately NOT stopwords: they carry the meaning that turns
# "X must happen" into "X must never happen", and dropping them made a
# contradiction look like a paraphrase.
STOP -= NEGATIONS


@dataclass
class Snippet:
    """One sentence of a document, with the evidence the audit needs."""

    file: str
    line: int  # 1-based
    text: str
    raw: str  # the full source line(s) this sentence came from
    when: int  # committer time of the commit that introduced the line

    @property
    def key(self) -> tuple[str, int]:
        return (self.file, self.line)


@dataclass
class Group:
    kind: str  # duplicate-command | near-duplicate | contradiction
    subject: str  # the command, flag, or the shared words
    members: list[Snippet] = field(default_factory=list)

    @property
    def newest(self) -> Snippet:
        return max(self.members, key=lambda s: (s.when, s.file, s.line))

    @property
    def older(self) -> list[Snippet]:
        newest = self.newest
        return [s for s in self.members if s.key != newest.key]


def _norm(text: str) -> list[str]:
    text = text.lower().replace("`", " ")
    text = re.sub(r"[^a-z0-9\s%._-]", " ", text)
    return [t for t in text.split() if t and t not in STOP]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #
def _blame_times(project: Path, rel: str) -> dict[int, int]:
    """Line -> committer time, from one `git blame` pass. {} when untracked."""
    try:
        out = subprocess.run(
            ["git", "blame", "--line-porcelain", "--", rel],
            cwd=project, capture_output=True, text=True, timeout=120,
        )
    except Exception:
        return {}
    if out.returncode != 0:
        return {}
    times: dict[int, int] = {}
    head = 0
    for line in out.stdout.splitlines():
        m = re.match(r"^([0-9a-f]{7,})\s+\d+\s+(\d+)", line)
        if m:
            head = int(m.group(2))
            continue
        if line.startswith("committer-time "):
            try:
                times[head] = int(line.split()[1])
            except ValueError:
                pass
    return times


def _sentences(raw: str) -> Iterable[str]:
    """Split a source line into sentences, keeping code spans intact."""
    # Only split on sentence punctuation followed by whitespace, so `--flag.`
    # inside a code span is not treated as a boundary.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z`(])", raw.strip())
    return [p.strip() for p in parts if p.strip()]


def load_snippets(project: Path) -> list[Snippet]:
    out: list[Snippet] = []
    for rel in DOC_FILES:
        path = project / rel
        if not path.is_file():
            continue
        times = _blame_times(project, rel)
        fallback = int(path.stat().st_mtime)
        in_fence = False
        for i, raw in enumerate(path.read_text().splitlines(), start=1):
            if raw.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or not raw.strip():
                continue
            # A markdown table row is an INDEX of commands, not a statement of a
            # rule: `| 7 | cli.py verify | code (gate) |` says nothing the prose
            # does not, and treating it as a claim made the audit flag every
            # command in the repo.
            if raw.lstrip().startswith("|"):
                continue
            when = times.get(i, fallback)
            for sent in _sentences(raw):
                out.append(Snippet(file=rel, line=i, text=sent, raw=raw, when=when))
    return out


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #
_CMD = re.compile(r"\b(?:devteam|cli\.py)\s+([a-z][a-z0-9-]{2,})")
_FLAG = re.compile(r"`(--[a-z][a-z0-9-]{2,})`")


def _multi_home(snippets: list[Snippet]) -> list[Group]:
    """A command documented in several files — reported, never auto-removed.

    Two docs can both legitimately mention `devteam verify` (a rule about it, an
    index row listing it), so a shared command token alone proves nothing. What is
    worth a human's eye is the case where the copies ALSO share content words —
    then they are two attempts at the same instruction and one is redundant.
    """
    by_token: dict[str, list[Snippet]] = {}
    for s in snippets:
        tokens = {f"devteam {m}" for m in _CMD.findall(s.text)}
        tokens |= {m for m in _FLAG.findall(s.text)}
        for t in tokens:
            by_token.setdefault(t, []).append(s)
    groups = []
    for token, members in by_token.items():
        first_per_file: dict[str, Snippet] = {}
        for m in sorted(members, key=lambda s: (s.file, s.line)):
            first_per_file.setdefault(m.file, m)
        if len(first_per_file) < 2:
            continue
        picked = list(first_per_file.values())
        common = set(_norm(picked[0].text))
        for m in picked[1:]:
            common &= set(_norm(m.text))
        if len(common) < 4:
            # Same command, unrelated sentences: an index row plus a rule. Not a
            # duplicate — flagging it teaches the team to ignore the report.
            continue
        groups.append(Group("multi-home", token, picked))
    return groups


def _sentence_groups(snippets: list[Snippet]) -> list[Group]:
    groups: list[Group] = []
    seen: set[tuple[str, int]] = set()
    enriched = []
    for s in snippets:
        toks = _norm(s.text)
        if len(toks) < MIN_TOKENS:
            continue
        enriched.append((s, set(toks)))
    for i, (a, ta) in enumerate(enriched):
        for b, tb in enriched[i + 1:]:
            if a.file == b.file or a.key in seen or b.key in seen:
                continue
            score = _jaccard(ta, tb)
            if score < NEAR_DUP:
                continue
            shared = ta & tb
            neg = lambda s: bool(set(_norm(s.text)) & NEGATIONS)  # noqa: E731
            kind = "contradiction" if neg(a) != neg(b) and len(shared) >= 3 else "near-duplicate"
            seen.add(a.key)
            seen.add(b.key)
            groups.append(Group(kind, ", ".join(sorted(shared)[:4]), [a, b]))
    return groups


def audit(project: Path, files: Iterable[str] | None = None) -> list[Group]:
    """Every overlap the docs currently carry, newest-wins applied at report time."""
    snippets = load_snippets(project)
    if files:
        wanted = set(files)
        snippets = [s for s in snippets if s.file in wanted]
        # A duplicate only counts if BOTH copies are in scope when a caller scopes
        # by file: the pre-commit hook must not "fix" a rule whose newer copy is
        # not in this commit.
        groups = _multi_home(snippets) + _sentence_groups(snippets)
        return [g for g in groups if all(m.file in wanted for m in g.members)]
    return _multi_home(snippets) + _sentence_groups(snippets)


# --------------------------------------------------------------------------- #
# removal
# --------------------------------------------------------------------------- #
def _sole_documentation(project: Path, token: str) -> bool:
    """True when removing this mention would leave the team with no doc for it."""
    snippets = load_snippets(project)
    hits = 0
    if token.startswith("--"):
        hits = sum(1 for s in snippets if token in s.text)
    else:
        cmd = token.split(" ", 1)[1] if token.startswith("devteam ") else token
        hits = sum(1 for s in snippets if re.search(rf"\b{re.escape(cmd)}\b", s.text))
    return hits <= 1


def _standalone(snippet: Snippet) -> bool:
    """True when deleting this snippet leaves a grammatical line behind.

    The docs are wrapped prose: a "sentence" extracted from line 130 is often the
    middle of a sentence that starts on 129 and ends on 131. Deleting it turned
    "pick warns on stderr / when the plan was not reviewed today -- a skipped step
    2 is visible... / and never a hard block" into "pick warns on stderr / and never
    a hard block", and a list item "✅ BUILD SHIPPED -- version bump pushed..." into
    " This". Automatic removal is therefore allowed only where the line IS the
    statement: a bullet or a line that starts and ends its own sentence.
    """
    line = snippet.raw.strip()
    body = re.sub(r"^(?:[-*+]|\d+\.)\s+(?:\*\*)?", "", line).strip()
    body = body.rstrip("*").strip()  # a bold lead-in keeps its marker in `raw`
    return body[:1].isupper() and body.endswith((".", "!", "?"))


def plan_removals(project: Path, groups: list[Group]) -> list[Snippet]:
    """The older copies to delete. Contradictions are never auto-removed."""
    doomed: dict[tuple[str, int], Snippet] = {}
    for g in groups:
        if g.kind == "contradiction":
            # The two sentences disagree about what is true. Which side survives is
            # a decision, not a merge.
            continue
        if g.kind == "multi-home":
            if _sole_documentation(project, g.subject):
                continue
            # Only a pure restatement is safe to delete: every content word of the
            # older sentence must already appear in the newer one, so the removal
            # provably loses nothing. Anything else is two different instructions
            # about one command and stays for a human.
            keep = set(_norm(g.newest.text))
            pure = all(
                (lambda old_tokens: old_tokens and
                 len(old_tokens & keep) / len(old_tokens) >= 0.95)(set(_norm(old.text)))
                for old in g.older
            )
            if not pure:
                continue
        for old in g.older:
            if not _standalone(old):
                continue
            doomed[old.key] = old
    return list(doomed.values())


def apply_removals(project: Path, doomed: list[Snippet]) -> list[dict[str, Any]]:
    """Delete the exact sentences from disk. Returns what was removed.

    A line that holds a duplicated sentence AND other text is rewritten, not
    dropped, so a shared bullet keeps the part that is not a second copy.
    """
    by_file: dict[str, list[Snippet]] = {}
    for s in doomed:
        by_file.setdefault(s.file, []).append(s)
    removed = []
    for rel, items in by_file.items():
        path = project / rel
        lines = path.read_text().splitlines(keepends=False)
        for s in sorted(items, key=lambda x: (x.line, -len(x.text)), reverse=True):
            idx = s.line - 1
            if idx >= len(lines):
                continue
            current = lines[idx]
            if s.text not in current:
                continue
            rest = current.replace(s.text, "", 1).rstrip()
            # Tidy a bullet that lost its body but kept its marker.
            stripped = re.sub(r"^\s*(?:[-*]|\d+\.)\s*$", "", rest)
            if stripped.strip():
                lines[idx] = stripped
            else:
                del lines[idx]
            removed.append({"file": rel, "line": s.line, "text": s.text[:160],
                            "when": s.when})
        path.write_text("\n".join(lines) + "\n")
    return removed


def format_report(project: Path, groups: list[Group]) -> str:
    if not groups:
        return "DOC OVERLAP: none — every requirement has exactly one home"
    lines = [f"DOC OVERLAP: {len(groups)} conflict(s) — newest text wins", ""]
    for g in groups:
        tag = {"multi-home": "ALSO", "near-duplicate": "SAME",
               "contradiction": "CONFLICT"}[g.kind]
        # Report what the audit knows, never what a fix might do: this report is
        # also printed by --check, where nothing has been removed (which used to
        # make people believe a duplicate was gone when it was still there).
        verdict = {"multi-home": "each copy must say something different",
                   "contradiction": "needs a decision",
                   "near-duplicate": "same statement — the older copy is redundant"}[g.kind]
        lines.append(f"  [{tag}] {g.subject}  — {verdict}")
        newest = g.newest
        for m in sorted(g.members, key=lambda s: -s.when):
            mark = "KEEP" if m.key == newest.key else "drop"
            lines.append(f"    {mark} {m.file}:{m.line}  {m.text[:150]}")
        lines.append("")
    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------- #
# ratchet: the existing overlaps are recorded, NEW ones block the commit
# --------------------------------------------------------------------------- #
BASELINE_REL = "scripts/docs_overlap_baseline.json"


def signature(group: Group) -> str:
    """Stable id for a group, independent of line numbers (they shift).

    Built from the normalised text of every member, so re-wrapping a paragraph or
    inserting a line above it does not look like a brand-new overlap.
    """
    import hashlib

    parts = sorted(" ".join(_norm(m.text)) for m in group.members)
    blob = group.kind + "|" + "|".join(parts)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def load_baseline(project: Path) -> dict[str, Any]:
    import json

    path = project / BASELINE_REL
    if not path.is_file():
        return {"groups": {}}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"groups": {}}


def save_baseline(project: Path, groups: list[Group]) -> Path:
    import json

    path = project / BASELINE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "_comment": ("Known doc overlaps, recorded so the audit can stop NEW ones without "
                     "pretending the old ones are fixed. Resolve one by making the two "
                     "documents say different things, then re-run "
                     "`devteam docs-audit --update-baseline`."),
        "groups": {signature(g): {"kind": g.kind, "subject": g.subject,
                                  "where": sorted(f"{m.file}:{m.line}" for m in g.members)}
                   for g in groups},
    }, indent=2, sort_keys=True) + "\n")
    return path


def new_overlaps(project: Path, groups: list[Group], staged: Iterable[str]) -> list[Group]:
    """Groups that are not in the baseline AND touch a file this commit changes."""
    known = set((load_baseline(project).get("groups") or {}).keys())
    touched = set(staged)
    return [g for g in groups
            if signature(g) not in known and any(m.file in touched for m in g.members)]


# --------------------------------------------------------------------------- #
# rule coverage: which rules are code, and which are still only prose
# --------------------------------------------------------------------------- #
RULES_REL = "scripts/rules.json"
KINDS = ("code", "enforcible", "judgment", "reference")


def load_rules(project: Path) -> list[dict[str, Any]]:
    import json

    path = project / RULES_REL
    if not path.is_file():
        return []
    return (json.loads(path.read_text()) or {}).get("rules") or []


def format_rules(project: Path) -> str:
    """The conversion backlog: what is already code, and what could be.

    This is the answer to "which prose should become code?" as data rather than an
    opinion — every rule the team enforces gets a kind, and the `enforcible` ones
    carry the proposal that would retire their prose.
    """
    rules = load_rules(project)
    if not rules:
        return f"RULE COVERAGE: no {RULES_REL}"
    counts = {k: sum(1 for r in rules if r.get("kind") == k) for k in KINDS}
    lines = [f"RULE COVERAGE: {len(rules)} rules — " +
             ", ".join(f"{counts[k]} {k}" for k in KINDS if counts[k])]
    lines.append("")
    for kind in KINDS:
        group = [r for r in rules if r.get("kind") == kind]
        if not group:
            continue
        lines.append(f"  [{kind.upper()}]")
        for r in group:
            tail = r.get("enforced_by") or r.get("proposal") or r.get("note") or ""
            lines.append(f"    {r['id']:<34} {r.get('doc', ''):<22} {tail}")
        lines.append("")
    todo = [r for r in rules if r.get("kind") == "enforcible"]
    if todo:
        lines.append(f"TO CONVERT: {len(todo)} prose rule(s) a command could enforce")
        for r in todo:
            lines.append(f"  • {r['rule']}")
            lines.append(f"    → {r.get('proposal')}")
    return "\n".join(lines).rstrip()
