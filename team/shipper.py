"""Shipper — generic. Version bump + whatsnew + commit + push origin main.
CI (owned by the project, not this team) then delivers to the Play
internal-testing track. Production promotion is human-only — this team
structurally has no path to it. The ONE owner-facing release question it raises
is "is this build a release candidate?": it is asked, recorded and shown, and
nothing in the pipeline acts on the answer (promoting stays the owner's manual
step in Play Console), so asking it never becomes a promotion request.

All project facts come from profile.json: version file, tag scheme, repo slug,
whatsnew path (and whether the owner curates those notes by hand), and the
allowed release-note length. The version bump regexes are the ONLY
project-shaped code here and they read the actual file rather than assuming its
shape.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .core import (read_delivery_file, state_of, RunLog, git, profile_of, run,
                   target_items, target_matches)


SEMVER_RX = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z][0-9A-Za-z.\-]*))?$")


def _parse_version(s: str) -> tuple[int, int, int] | None:
    """The numeric triple of a version name, prerelease suffix ignored.

    `1.0.9-rc.1` is (1, 0, 9): the suffix is a CHANNEL, and it must never leak
    into the arithmetic that builds a versionCode.
    """
    parsed = parse_version_name(s)
    return parsed[:3] if parsed else None


def parse_version_name(s: str) -> tuple[int, int, int, str] | None:
    """(major, minor, patch, prerelease) — prerelease is "" for a final build."""
    m = SEMVER_RX.match(str(s or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), m.group(4) or "")


def _pre_index(pre: str) -> int:
    """`rc.3` / `rc3` / `rc-3` → 3; anything unrecognised → 0."""
    m = re.search(r"(\d+)$", pre or "")
    return int(m.group(1)) if m else 0


def remote_latest_release(project: Path) -> dict | None:
    """The highest version tag on origin, prerelease-aware.

    Ordering is SemVer's: a prerelease sorts BELOW its own final version
    (`1.0.9-rc.2 < 1.0.9`), and above any earlier final. Getting this wrong is
    how a chain of rc builds makes the next release compute the wrong number.
    """
    prefix = profile_of(project).get("tag_prefix", "v")
    r = git(["ls-remote", "--tags", "origin", f"refs/tags/{prefix}*"],
            cwd=project, timeout=60)
    if r.returncode != 0:
        return None
    rx = rf"refs/tags/{re.escape(prefix)}(\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z][0-9A-Za-z.\-]*)?)$"
    found: list[dict] = []
    for name in re.findall(rx, r.stdout, re.M):
        parsed = parse_version_name(name)
        if not parsed:
            continue
        major, minor, patch, pre = parsed
        found.append({"version": name, "major": major, "minor": minor,
                      "patch": patch, "pre": pre, "pre_index": _pre_index(pre)})
    if not found:
        return None
    # Final > prerelease for the same triple; then compare the triple.
    return max(found, key=lambda v: (v["major"], v["minor"], v["patch"],
                                     v["pre"] == "", v["pre_index"]))


def remote_latest_version(project: Path) -> tuple[int, int, int] | None:
    latest = remote_latest_release(project)
    return None if latest is None else (latest["major"], latest["minor"], latest["patch"])


def next_version_name(project: Path, rc: bool) -> tuple[str, str]:
    """(version_name, why) for the next build — the ONE place that decides it.

    `rc=True` continues (or opens) a prerelease chain for the next patch:
    1.0.8 → 1.0.9-rc.1 → 1.0.9-rc.2. `rc=False` closes an open chain with its
    final version (1.0.9), or starts a fresh patch when there is no chain open.
    """
    latest = remote_latest_release(project)
    if latest is None:
        raise SystemExit("no version tags on origin; cannot compute the next version")

    if latest["pre"]:
        triple = f"{latest['major']}.{latest['minor']}.{latest['patch']}"
        if rc:
            nxt = latest["pre_index"] + 1
            return (f"{triple}-rc.{nxt}",
                    f"continues the open rc chain ({latest['version']} → -rc.{nxt})")
        return (triple, f"closes the open rc chain {latest['version']} as its final build")

    triple = f"{latest['major']}.{latest['minor']}.{latest['patch'] + 1}"
    if rc:
        return (f"{triple}-rc.1", f"opens an rc chain after {latest['version']}")
    return (triple, f"next patch after {latest['version']}")


def next_version_code(state: dict, play_floor: int = 0) -> int:
    """The next versionCode: monotonic, independent of the version NAME.

    A prerelease and its eventual final are different uploads and Play refuses a
    reused code, so the code cannot be derived from the semver triple alone.
    It is the highest of: what Play holds (floor), what the ledger last used.
    """
    used = 0
    for row in state.get("shipped") or []:
        code = row.get("version_code")
        if isinstance(code, int) and code > used:
            used = code
        triple = _parse_version(str(row.get("version") or ""))
        if triple:
            used = max(used, triple[0] * 10000 + triple[1] * 100 + triple[2])
    last = state.get("last_version_code")
    if isinstance(last, int) and last > used:
        used = last
    return max(play_floor, used) + 1


def play_max_version_code(project: Path) -> tuple[int, str]:
    """Highest versionCode Play already has for this app, or (0, why-not).

    The tag stack in git says what the TEAM released; Play says what the STORE
    accepted. Those can differ (a build uploaded outside the pipeline, a CI run
    that died after upload), and Play rejects a duplicate or lower versionCode
    with a hard failure at the very end of a release. So the store is the floor
    for the next code; it is read with the service account the project already
    declares for vitals/reviews, and a missing key simply means "no floor" —
    a release must never depend on a third party being reachable.
    """
    from .research import _play_get, _play_token
    profile = profile_of(project)
    conns = profile.get("connections") or {}
    for key in ("play_vitals", "play_reviews"):
        conf = conns.get(key) or {}
        pkg = conf.get("package")
        if not pkg:
            continue
        token, why = _play_token(conf, "https://www.googleapis.com/auth/androidpublisher", project)
        if not token:
            return 0, f"{key}: {why}"
        api = "https://androidpublisher.googleapis.com/androidpublisher/v3"
        edit = _play_get(f"{api}/applications/{pkg}/edits", project, token, body={})
        edit_id = edit.get("id")
        if not edit_id:
            return 0, f"{key}: could not open an edit ({edit.get('_error') or 'no id'})"
        try:
            bundles = _play_get(f"{api}/applications/{pkg}/edits/{edit_id}/bundles", project, token)
            codes = [int(b.get("versionCode", 0)) for b in (bundles.get("bundles") or [])]
            return (max(codes), f"{pkg} on Play") if codes else (0, f"{pkg}: no bundles uploaded")
        finally:
            # discard the edit: an uncommitted edit is a stray, and committing it
            # would be a write where a read was intended
            from .core import run
            run(["curl", "-s", "-X", "DELETE", "-H", f"Authorization: Bearer {token}",
                 f"{api}/applications/{pkg}/edits/{edit_id}"], project, timeout=60)
    return 0, "no Play connection declared"


def bump_version_file(worktree: Path, new, log: RunLog,
                      floor_code: int = 0, code: int | None = None) -> str:
    """Write `new` (a version NAME string) + its versionCode into the version file.

    The name is written verbatim, so `1.0.9-rc.1` is a legal release: the
    prerelease suffix is a channel marker, and only the numeric triple feeds the
    fallback code. `code` (when given) wins outright — `release` allocates it
    monotonically so a prerelease and its final never share a Play versionCode.
    """
    profile = profile_of(worktree)
    gf = worktree / profile["version_file"]
    text = gf.read_text()
    name = str(new).strip()
    triple = _parse_version(name)
    if triple is None:
        raise SystemExit(f"not a version name: {name!r} (expected MAJOR.MINOR.PATCH[-prerelease])")
    explicit_code = code is not None
    code = code if code is not None else triple[0] * 10000 + triple[1] * 100 + triple[2]
    if floor_code >= code and not explicit_code:
        # Play already holds this code (or a higher one) — the upload would be
        # rejected as "version code already used".
        code = floor_code + 1
        log(f"versionCode floor from Play: {floor_code} -> using {code}")

    # Generic: bump whichever versionName/versionCode constants exist in the file
    # (Android Gradle convention used by this project; CI parses them back).
    # versionName may be 1-3 segments (some projects use "1.4", most "1.2.3");
    # always write major.minor.patch — CI computes the code from the same scheme.
    new_text, n_name = re.subn(
        r'(versionName[\s\S]{0,70}?["\'])(\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z][0-9A-Za-z.\-]*)?)(["\'])',
        rf"\g<1>{name}\g<3>", text, count=1)

    if profile.get("version_code_bump") == "increment":
        # Some projects encode info in the versionCode beyond the semver value —
        # a per-flavor minSdk prefix (so Play routes each device to the right
        # bundle) is one example. Overwriting those with the single semver `code`
        # would collapse two flavors onto one code, so bump EVERY versionCode
        # literal by +1 instead (profile-driven; default stays the semver
        # overwrite below).
        new_text, n_code = re.subn(
            r'(versionCode[^\n]{0,30}?[?:=\s])(\d+)(\s*\)?)',
            lambda m: f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}",
            new_text)
        if n_name != 1:
            log(f"FATAL: could not bump versionName in {profile['version_file']} — refusing to ship")
            raise SystemExit("version bump failed — inspect the version file")
        if n_code:
            log(f"{bump_version_file.__name__}: bumped {n_code} versionCode(s) by +1")
        else:
            log(f"{bump_version_file.__name__}: WARNING no versionCode literal bumped "
                "(version_code_bump=increment)")
        gf.write_text(new_text)
        return name

    new_text, n_code = re.subn(
        r'(versionCode[^\n]{0,70}?[?:=\s])(\d+)(\s*\)?)',
        rf"\g<1>{code}\g<3>", new_text, count=1)
    if n_name != 1:
        log(f"FATAL: could not bump versionName in {profile['version_file']} — refusing to ship")
        raise SystemExit("version bump failed — inspect the version file")
    if n_code == 1:
        log(bump_version_file.__name__ + ": also bumped versionCode")
    gf.write_text(new_text)
    return name


# Play Console rejects a release note longer than this (per locale). It is a
# hard store limit, not a style choice — the reference CI workflow carries the
# same number as a guard step, and this is the copy the pipeline writes.
WHATS_NEW_MAX_CHARS = 500

# A "what's new" entry is a storefront blurb, not the task title: the first
# clause of the label is the user-facing gist, and the whole line stays short so
# the note has room for a second bullet inside the cap above.
_WHATS_NEW_LABEL_CHARS = 60


def whatsnew_max_chars(profile: dict) -> int:
    """The allowed length of a release note, per project (default 500)."""
    try:
        n = int(profile.get("whatsnew_max_chars") or WHATS_NEW_MAX_CHARS)
    except (TypeError, ValueError):
        n = WHATS_NEW_MAX_CHARS
    return n if n > 0 else WHATS_NEW_MAX_CHARS


def _short_label(label, limit: int = _WHATS_NEW_LABEL_CHARS) -> str:
    """An internal task label as a storefront phrase.

    Labels are written for the pipeline, not for a user: they run to 200+ chars
    and often carry the mechanism after a separator ("One result surface for all
    five jobs: a shared ResultCard + action stack showing …"). Shipping the raw
    label put that whole sentence in front of users. Take the first clause and
    cut on a word boundary.

    The label's own casing is KEPT: lowercasing the first letter to fit a
    mid-sentence slot turned "PDF-Kit: home identity" into "pDF-Kit", and a
    mangled product name in a store listing is worse than a capital letter after
    a colon. The templates below label the line instead of embedding it.
    """
    text = _first_clause(label)
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = _strip_dangling(cut or text[:limit])
    return text or "the app"


def _first_clause(label) -> str:
    """The label up to its first separator, whitespace-collapsed."""
    text = re.split(r"\s*[—–:|]\s*", str(label or "").strip())[0].strip()
    return re.sub(r"\s+", " ", text)


# A word that cannot end a sentence: cutting a label at a width and leaving one
# of these behind ships copy like "Fixed: Pin the untested spine and the
# paywall's honesty a" (v3.13.74 did exactly this — the article was part of the
# next noun, which the cut had removed).
_DANGLING_TAIL = {
    "a", "an", "the", "and", "or", "but", "nor", "of", "to", "for", "with",
    "in", "on", "at", "by", "from", "into", "onto", "over", "under", "as",
    "than", "that", "which", "while", "when", "its", "their", "his", "her",
}


def _strip_dangling(text: str) -> str:
    """Drop trailing separators and any function word left with no noun after it."""
    out = str(text or "").rstrip(" ,;.-")
    while out:
        head, _, last = out.rpartition(" ")
        if last.lower() not in _DANGLING_TAIL:
            break
        out = head
    return out.rstrip(" ,;.-")


def label_was_truncated(label, limit: int = _WHATS_NEW_LABEL_CHARS) -> bool:
    """True when the derived storefront phrase is not the label's own clause.

    A truncated label is a fragment ("…the paywall's honesty"), and a fragment
    is not a release note: the templates below fall back to the honest generic
    note instead of shipping a sentence that stops mid-thought.
    """
    return len(_first_clause(label)) > limit


# A router/queue label is an identifier ("visual-complete-the-testing-and-2026-11-05"),
# not store copy. Deriving the note from one put that slug in front of testers.
_INTERNAL_LABEL_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")
_INTERNAL_KIND_PREFIXES = ("feature-", "bugfix-", "visual-", "brand-", "store-",
                           "release-", "direction-")

_UNDER_THE_HOOD_NOTE = (
    "✓ Under-the-hood reliability and performance improvements\n"
    "✓ Everything you already use keeps working the same way\n"
)

# A queue label may be perfectly readable English and still be pipeline
# vocabulary: v1.5.40 shipped the play note "Fixed: Every declared screen is
# rendered and its honest states are asserted", which tells a user nothing about
# what changed and exposes the team's own test machinery. A note is judged by a
# different standard from a label, so the derived line is checked for engineering
# terms the storefront must never carry; a hit falls back to the honest generic
# note, and a task spec that wants specific user copy supplies the `whatsnew`
# field (which always wins).
_INTERNAL_COPY_RE = re.compile(
    r"\b(declared|assertion|assertions|asserted|asserts?|composables?|harness|"
    r"tests?|"
    r"scenes?|renders?|rendered|rendering|unit[- ]tests?|instrumented|"
    r"roborazzi|robolectric|coverage|refactor(?:ed|ing)?|codegen|"
    r"gradle|kotlin|versionCode|version code|api level|lint|"
    r"smoke tests?|test suite|test file|diff)\b",
    re.IGNORECASE,
)


def _is_internal_copy(text: str) -> bool:
    """True when a derived note line describes the pipeline, not the product."""
    return bool(_INTERNAL_COPY_RE.search(str(text or "")))


def _is_internal_label(label: str) -> bool:
    """True when `label` is an internal identifier rather than user-facing copy."""
    text = (label or "").strip()
    if not text or " " in text:
        return False
    if text.startswith(_INTERNAL_KIND_PREFIXES):
        return True
    return bool(_INTERNAL_LABEL_RE.match(text))


def _trim_whatsnew(text: str, max_chars: int) -> tuple[str, bool]:
    """Clamp a note to the allowed length, cutting on a line boundary when the
    note is bulleted so no bullet is lost mid-word. Returns (text, trimmed)."""
    if len(text) <= max_chars:
        return text, False
    body = text[:max_chars]
    lines = body.rstrip().splitlines()
    if len(lines) > 1:
        lines = lines[:-1] or [body]
        kept = "\n".join(lines)
        return kept + "\n", True
    cut = body.rsplit(" ", 1)[0] if " " in body else body
    return cut.rstrip() + "\n", True


def _whatsnew_text(task: dict, max_chars: int = WHATS_NEW_MAX_CHARS) -> str:
    """Honest Play release notes per task TYPE, within the store's length limit.

    The motion template used to be written for every release, so a feature or
    bugfix release claimed "No functional changes — all existing features behave
    exactly as before" (false, and exactly backwards for a behaviour change).
    A spec may supply an explicit `whatsnew` string; otherwise this per-type
    default is used. Everything is clamped to `max_chars`, because Play rejects
    an over-long note and a rejected upload is a failed release.
    """
    explicit = (task.get("whatsnew") or "").strip()
    if explicit:
        text = explicit if explicit.endswith("\n") else explicit + "\n"
    else:
        label = _short_label((task.get("surface", {}) or {}).get("label"))
        raw_label = (task.get("surface", {}) or {}).get("label")
        ttype = task.get("type", "motion")
        if (_is_internal_label(label) or _is_internal_copy(label)
                or label_was_truncated(raw_label)):
            # An id — or a sentence about declared screens, scenes and assertions,
            # or a label cut to a fragment — is not a release note: say what
            # actually changed for the user instead of printing the pipeline's
            # own vocabulary.
            text = _UNDER_THE_HOOD_NOTE
        elif ttype == "motion":
            text = (f"✓ Polished: {label}\n"
                    "✓ No functional changes — everything works exactly as before\n")
        elif ttype == "bugfix":
            text = (f"✓ Fixed: {label}\n"
                    "✓ Nothing else changed — everything you already use behaves the same way\n")
        else:
            # feature / anything else that changes behaviour: never claim "no changes"
            text = (f"✓ New in this update: {label}\n"
                    "✓ Everything you already use keeps working the same way\n")
    return _trim_whatsnew(text, max_chars)[0]


def _normalize_task(task) -> dict:
    """A task spec dict, or a bare label string (legacy caller shape)."""
    if isinstance(task, str):
        return {"type": "motion", "surface": {"label": task}}
    return task if isinstance(task, dict) else {}


def _explicit_whatsnew(task) -> str:
    return (str(_normalize_task(task).get("whatsnew") or "")).strip()


def whatsnew_preview(project: Path, task, root: Path | None = None) -> str:
    """Exact note a release WOULD ship for `task`, without writing anything.

    Three sources, one order:
      1. an explicit per-release `whatsnew` in the task spec — always wins,
         including on an owner-curated project (the spec is this release's
         authored copy, not the boilerplate template the curation protects
         against);
      2. on a curated project with no authored copy, the file on disk
         (`root`, the worktree when the caller has one);
      3. otherwise the per-type template.

    Split out of `write_whatsnew` so the release gate can judge the note
    BEFORE anything is written or bumped: a blocked release must leave the
    tree exactly as clean as it found it.
    """
    profile = profile_of(project)
    rel = profile.get("whatsnew_file")
    if not rel:
        return ""
    if profile.get("whatsnew_curated") and not _explicit_whatsnew(task):
        wf = Path(root or project) / rel
        return wf.read_text() if wf.exists() else ""
    return _whatsnew_text(_normalize_task(task), whatsnew_max_chars(profile))


def shipped_notes(project: Path, rel: str, limit: int = 2) -> list[tuple[str, str]]:
    """(version, committed note) for the most recent distinct SHIPPED versions,
    newest first. The ledger, not tags: a tag is pushed before CI runs, so a
    tag whose upload failed never reached a user and must not count as "the
    store's current text"."""
    state = state_of(project)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for entry in reversed(state.get("shipped") or []):
        version = str(entry.get("version") or "")
        commit = str(entry.get("commit") or "")
        if not version or version in seen or not commit:
            continue
        r = git(["show", f"{commit}:{rel}"], cwd=project, timeout=60)
        if r.returncode != 0 or not r.stdout.strip():
            continue
        seen.add(version)
        out.append((version, r.stdout))
        if len(out) >= limit:
            break
    return out


def previous_shipped_note(project: Path, rel: str) -> tuple[str, str]:
    """(version, committed note) of the last release that actually SHIPPED."""
    tail = shipped_notes(project, rel, limit=1)
    if tail:
        return tail[0]
    # No usable ledger row (fresh clone, a project that records by hand): fall
    # back to the latest tag, which is still a real user-visible release.
    latest = remote_latest_release(project)
    if latest:
        prefix = profile_of(project).get("tag_prefix", "v")
        r = git(["show", f"{prefix}{latest['version']}:{rel}"], cwd=project, timeout=60)
        if r.returncode == 0 and r.stdout.strip():
            return latest["version"], r.stdout
    return "", ""


def whatsnew_gate(project: Path, task, root: Path | None = None,
                  log: RunLog | None = None) -> tuple[bool, str]:
    """Refuse a release whose store note would repeat the previous one verbatim.

    THE DEFECT THIS CLOSES: `whatsnew_curated` was a freeze — the pipeline read
    the file and never wrote it, and nothing but a warning line in the handover
    ever looked at it. One project shipped 28 consecutive releases behind a
    single old note, so every "What's new" on its listing was the same text.
    A stale note is now a failing gate (verify + release), not a warning: the
    remedy is one line in the task spec's `whatsnew`, which the orchestrator
    owns and can write without the owner.
    """
    profile = profile_of(project)
    rel = profile.get("whatsnew_file")
    if not rel:
        return True, "no release-note file declared"
    note = whatsnew_preview(project, task, root=root).strip()
    if not note:
        return False, (f"'{rel}' is empty — Play will show no release note for this "
                       "build. Write this release's line into the task spec's "
                       '"whatsnew" field (or the note file) before releasing.')
    prev_version, prev_note = previous_shipped_note(project, rel)
    if prev_note.strip() and prev_note.strip() == note:
        return False, (
            f"this release's note is identical to the one shipped in v{prev_version} — "
            "the store would show the previous release's words again. Give this release "
            f'its own line in the task spec\'s "whatsnew" field (or edit {rel}) and re-run. '
            "A curated note is the owner's last word, not a frozen one.")
    detail = (f"differs from v{prev_version}'s note" if prev_note.strip()
              else "no previous shipped note to compare")
    if log:
        log(f"whatsnew gate: ok — {detail}")
    return True, detail


def write_whatsnew(worktree: Path, task, log: RunLog | None = None) -> str | None:
    """Write the release note for `task`. Accepts a task spec dict, or a bare
    label string (legacy caller shape → motion template). Returns what the file
    now holds (None when the project ships no note).

    A project whose profile sets `whatsnew_curated` owns these notes by hand: the
    pipeline never invents the boilerplate template for it. It DOES write an
    explicit per-release `whatsnew` from the task spec — that is this release's
    authored copy, and refusing it is what froze one project's note for 28
    releases. `whatsnew_gate` runs before this, so the note only lands when it
    differs from the last shipped one.
    """
    task = _normalize_task(task)
    profile = profile_of(worktree)
    wf_rel = profile.get("whatsnew_file")
    if not wf_rel:
        return None
    wf = worktree / wf_rel
    if not wf.exists():
        return None
    explicit = _explicit_whatsnew(task)
    if profile.get("whatsnew_curated") and not explicit:
        text = wf.read_text()
        if log:
            log(f"whatsnew: '{wf_rel}' is owner-curated and this spec carries no "
                f"authored note — kept as-is ({len(text)} chars)")
        return text
    if profile.get("whatsnew_curated") and log:
        log(f"whatsnew: owner-curated project, but this spec carries an explicit "
            f"note — writing the release's own copy")
    cap = whatsnew_max_chars(profile)
    text = _whatsnew_text(task, cap)
    if log and not explicit and _is_internal_label(
            _short_label((task.get("surface", {}) or {}).get("label"))):
        log("whatsnew: task label is an internal id — shipping the generic "
            "under-the-hood note; add a user-facing `whatsnew` to the task spec")
    if log:
        raw = _whatsnew_text(task, 10 ** 9)
        if len(raw) > cap:
            log(f"whatsnew: trimmed {len(raw)} → {len(text)} chars (limit {cap})")
        else:
            log(f"whatsnew: {len(text)}/{cap} chars")
    wf.write_text(text)
    return text


def whatsnew_read(project: Path, commit: str = "") -> str:
    """The release note that actually SHIPPED for `commit` (the committed copy,
    not whatever is in a working tree), so a run report quotes the real note.
    Falls back to the project's own file, then to ''."""
    rel = profile_of(project).get("whatsnew_file")
    if not rel:
        return ""
    if commit:
        r = git(["show", f"{commit}:{rel}"], cwd=project, timeout=60)
        if r.returncode == 0:
            return r.stdout
    # No commit given (e.g. `devteam rc` after the worktree is gone): the SHIPPED
    # copy lives at origin/main. Reading the owner's checkout instead quotes a
    # note from however many commits ago it was last pulled — on 2026-10-30 a
    # shipped note was reported as the previous release's "maintenance release".
    shipped = read_delivery_file(project, rel, fallback_to_checkout=False)
    if shipped:
        return shipped
    p = Path(project) / rel
    return p.read_text() if p.exists() else ""


def whatsnew_test_areas(task: dict, limit: int = 6) -> list[str]:
    """Human-readable places this release changed, for the run report's manual
    test list. Derived from the task's target paths — never invented: a glob
    shows as the folder it covers, a file as its own name, and the common
    directory prefix is dropped so the list reads as 'feature/split/…'.

    A spec's `targets` is sometimes a LIST holding one comma-joined string (the
    queue stores `target` that way and `pick` wraps it), so each item is split
    again on commas — a path can never contain one, but a joined list always
    does."""
    raw_items: list[str] = []
    for item in target_items((task.get("surface", {}) or {}).get("targets")):
        raw_items.extend(p.strip() for p in item.split(",") if p.strip())
    items: list[str] = []
    for raw in raw_items:
        t = raw.rstrip("/")
        if t.endswith("/**"):
            t = t[:-3]
        if t and t not in items:
            items.append(t)
    if len(items) > 1:
        prefix = os.path.commonprefix(items).rsplit("/", 1)[0]
        if prefix:
            items = [i[len(prefix) + 1:] if i.startswith(prefix + "/") else i for i in items]
    return [i[3:] if i.startswith("**/") else i for i in items][:limit]


def bump_subject(task: dict, new_version: str) -> str:
    """Commit subject for the version-bump commit, named for the task's real
    kind. It used to say "... motion" for every release, so a feature or bugfix
    release mislabelled history (`git log`) even though the Play release notes
    were already type-aware."""
    label = (task.get("surface", {}) or {}).get("label") or "app"
    kind = task.get("type", "motion")
    return f"chore: bump version to {new_version} for {str(label).lower()} {kind} (dev-team)"


def push_and_deliver(project: Path, worktree: Path, task: dict, new_version: str,
                     log: RunLog, max_retries: int = 3, code: int | None = None) -> dict:
    """Push the release commit/tag and monitor CI until it succeeds.

    A retry RE-RUNS THE SAME WORKFLOW for the SAME version. It used to re-bump the
    version on every attempt, which is the worst of both worlds: a flaky upload
    could ship `1.0.10` when the artifact that was tested and reviewed was
    `1.0.9`, and the version you approved never existed in the store. A version
    is fixed by its release commit; if the run cannot be re-run, the honest
    answer is "fix it and release again", which allocates a new version openly
    instead of silently.
    """
    profile = profile_of(project)
    branch = profile["main_branch"]
    run_id: str | None = None

    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            if not run_id:
                return {"delivered": False, "version": new_version,
                        "error": "CI failed and the run id is unknown — cannot re-run; "
                                 "fix the cause and release again (a new version is allocated openly)",
                        "ci": {}}
            log(f"retry {attempt}/{max_retries}: re-running CI run {run_id} for v{new_version} "
                "(same version, same commit)")
            rr = run(["gh", "run", "rerun", str(run_id), "-R", profile["repo_slug"]],
                     project, timeout=120)
            if rr.returncode != 0:
                return {"delivered": False, "version": new_version,
                        "error": f"could not re-run CI run {run_id}: {(rr.stdout + rr.stderr)[-160:]}",
                        "ci": {}}
            ci = await_ci(project, {"commit": _pushed_sha(worktree), "run_id": str(run_id)})
            if ci.get("conclusion") == "success":
                return {"delivered": True, "commit": _pushed_sha(worktree),
                        "version": new_version, "ci": ci}
            continue

        # An untracked file in the worktree is staged by `git add -A` and rides
        # into the release commit. A crashed or interrupted developer session can
        # leave scratch files behind (a stray `.diskprobe` was found this way), and
        # scope_check cannot catch them: it blocks FROZEN paths, and a stray file is
        # under no path at all. A file INSIDE the task's allow-list is the task's own
        # new source (a new test, a new composable) and is expected — warning about
        # those drowned the real signal: a run whose whole deliverable was seven new
        # files warned that all seven "belong to no task". So classify first, and be
        # loud only about what is outside the allow-list.
        untracked = [f for f in git(["ls-files", "--others", "--exclude-standard"],
                                    cwd=worktree).stdout.splitlines() if f.strip()]
        targets = (task.get("surface", {}) or {}).get("targets")
        outside = [f for f in untracked if not target_matches(f, targets)]
        inside = [f for f in untracked if target_matches(f, targets)]
        if outside:
            shown = ", ".join(outside[:8]) + (" …" if len(outside) > 8 else "")
            log(f"WARNING: {len(outside)} untracked file(s) OUTSIDE the task's allow-list "
                f"will be committed with this change — check they are not scratch left by a "
                f"crashed session: {shown}")
        if inside:
            log(f"  {len(inside)} new file(s) inside the task's allow-list → committed with the change")

        git(["add", "-A"], cwd=worktree)
        rc = git(["commit", "-m", bump_subject(task, new_version)], cwd=worktree)
        if rc.returncode != 0:
            return {"delivered": False, "error": "bump commit failed", "ci": {}}

        sha = git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()
        log(f"pushing {sha[:8]} → origin {branch} "
            "(CI → build → internal testing)")
        r = git(["push", "origin", f"HEAD:{branch}"], cwd=worktree, timeout=180)
        if r.returncode != 0:
            log("push rejected — remote moved since base; rebasing")
            git(["fetch", "origin"], cwd=project, timeout=120)
            rb = git(["rebase", f"origin/{branch}"], cwd=worktree, timeout=180)
            if rb.returncode != 0:
                git(["rebase", "--abort"], cwd=worktree)
                return {"delivered": False, "error": "push rejected and rebase failed", "ci": {}}
            r = git(["push", "origin", f"HEAD:{branch}"], cwd=worktree, timeout=180)
            if r.returncode != 0:
                return {"delivered": False, "error": "push failed twice", "ci": {}}
        log("pushed to origin main")
        # The rebase REWROTE the commit: `sha` was read before the push, so a
        # rejected push left it pointing at a commit that no longer exists and
        # `await_ci` then waited 30 minutes for a CI run that could never appear
        # (observed 2026-11-04: the run was green, the release hung). Re-read it
        # from the tree that was actually pushed.
        sha = git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip() or sha
        # Tag-gated CI: some projects release on `v*` tags only — the bump push
        # alone would never deliver. Push the version tag when profile says so.
        if profile.get("ci_trigger", "bump") == "tag":
            tag = f'{profile.get("tag_prefix", "v")}{new_version}'
            r = git(["tag", tag], cwd=worktree)
            if r.returncode != 0:
                log(f"tag {tag} not created locally (may already exist)")
            else:
                r = git(["push", "origin", tag], cwd=worktree, timeout=120)
                if r.returncode == 0:
                    log(f"pushed tag {tag} → CI (tag-gated delivery)")
                else:
                    log(f"tag push failed: {(r.stdout + r.stderr)[-200:]}")
        ci = await_ci(project, {"commit": sha})
        run_id = str(ci.get("run_id") or "") or None

        if ci.get("conclusion") == "success":
            return {"delivered": True, "commit": sha, "version": new_version,
                    "code": code, "ci": ci}

        if attempt < max_retries:
            log(f"CI {ci.get('conclusion', 'unknown')} on attempt {attempt}/{max_retries} — retrying")
        else:
            log(f"CI failed after {max_retries} attempts — giving up")

    return {"delivered": False, "error": f"CI failed after {max_retries} attempts",
            "ci": ci, "commit": sha, "version": new_version}


def _pushed_sha(worktree: Path) -> str:
    return git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()


def _ci_runs(project: Path, slug: str, wf: str, how: int = 8) -> list[dict]:
    import json
    r = run(["gh", "run", "list", "-R", slug, "--workflow", wf, "-L", str(how),
             "--json", "databaseId,status,conclusion,headSha"], project, timeout=60)
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout or "[]")
    except ValueError:
        return []


def await_ci(project: Path, target, log: RunLog | None = None,
             max_wait_s: int = 1800) -> dict:
    """Wait for the CI run for `target`; return its conclusion AND its run id.

    `target` is a commit sha (string) or {"commit": sha, "run_id": id}. The run id
    matters because a retry re-runs that exact run instead of minting a new
    version, so it is returned on every completed answer.
    """
    if isinstance(target, str):
        target = {"commit": target}
    sha = str(target.get("commit") or "")
    want_run = str(target.get("run_id") or "")
    profile = profile_of(project)
    slug = profile["repo_slug"]
    wf = profile["ci_workflow"]
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        for e in _ci_runs(project, slug, wf):
            run_id = str(e.get("databaseId") or "")
            if want_run and run_id != want_run:
                continue
            if not want_run and sha and e.get("headSha") != sha:
                continue
            if e.get("status") != "completed":
                continue
            if log:
                log(f"CI {(sha or run_id)[:8]}: {e.get('conclusion')} — internal testing "
                    + ("updated" if e.get("conclusion") == "success" else "NOT updated"))
            return {"status": "completed", "conclusion": e.get("conclusion"),
                    "run_id": run_id}
        time.sleep(60)
    if log:
        log("CI still running — alarm will reconcile later")
    return {"status": "pending"}
