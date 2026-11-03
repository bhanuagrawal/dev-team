"""The report page: what changed in every app, and what to test — served locally.

One static page, regenerated from facts that already exist on this machine: each
project's `.pipeline/state.json` ledger (release, rc verdict, versionCode), the
release tags in git, the diff each tag introduced, the handover markdown the
release step wrote, and the task specs. Nothing here asks a model to summarise:
"what to test" is DERIVED from what the diff touched, which is the only version of
that answer that cannot drift from the build.

Served over the tailnet by `devteam site serve` (no public exposure): the page is
for the owner's phone on a roof, not for the internet.

Why a file and not a dashboard: the release loop already prints everything once,
in a chat nobody re-reads a week later. This is the same facts, kept.
"""
from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path

from .core import PROJECTS_BASES, git, profile_of, state_of

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
TAGS_PER_APP = 12

# A path in the diff maps to a surface a human can walk. Order matters: the first
# rule that matches wins, so the specific ones come before the generic.
AREA_RULES: list[tuple[str, str]] = [
    (r"/ui/theme/", "theme (palette, type, both normal and sunlight mode)"),
    (r"/ui/dashboard/", "dashboard (audits list, checklists, defects, certificates, settings)"),
    (r"/ui/inspection/", "inspection detail (checklist items, math panels, defect evidence, signatures)"),
    (r"/ui/markup/", "photo markup"),
    (r"/ui/signature/", "signature capture"),
    (r"/ui/components/", "shared components (status chips, buttons, dictation)"),
    (r"/calc/", "the maths engine (reactive calculations and their verdicts)"),
    (r"/data/", "local storage (Room entities, DAO, repository)"),
    (r"/pdf/", "the generated PDF certificate"),
    (r"/model/", "checklist templates and domain models"),
    (r"/src/test/", "unit tests"),
    (r"/src/androidTest/", "on-device tests"),
    (r"^\.github/workflows/", "CI"),
    (r"^docs/", "docs"),
    (r"build\.gradle", "the build (version, dependencies, SDK)"),
    (r"AndroidManifest\.xml", "the manifest (permissions, providers, components)"),
]

SCREEN_RX = re.compile(r"/([A-Z][A-Za-z0-9]*(?:Screen|Sheet|Dialog|Panel|Bar|Overlay|Picker))\.kt$")
TEST_RX = re.compile(r".*/([A-Z][A-Za-z0-9]*Test)\.kt$")

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; background:#080b10; color:#e6edf3;
  font: 14px/1.5 -apple-system, "SF Mono", ui-monospace, monospace; }
a { color:#3dffa2; text-decoration:none; }
a:hover { text-decoration:underline; }
header { padding:18px 20px; border-bottom:1px solid #26303b; background:#0d1117;
  position:sticky; top:0; z-index:5; }
h1 { margin:0 0 4px; font-size:17px; letter-spacing:.5px; }
.sub { color:#8b98a5; font-size:12px; }
main { padding:20px; max-width:1080px; margin:0 auto; }
.app { display:block; padding:14px 16px; margin-bottom:10px; border:1px solid #26303b;
  border-radius:8px; background:#0d1117; }
.app:hover { border-color:#3dffa2; text-decoration:none; }
.row { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; align-items:baseline; }
.name { font-size:15px; color:#fff; font-weight:700; }
.ver { color:#3dffa2; font-weight:700; }
.meta { color:#8b98a5; font-size:12px; }
.badge { font-size:11px; font-weight:700; padding:2px 7px; border-radius:4px; }
.rc { background:#00351f; color:#8fffc4; border:1px solid #3dffa2; }
.final { background:#12202b; color:#9fb3c8; border:1px solid #2b3a50; }
.no { background:#3a2400; color:#ffe1a3; border:1px solid #ffc233; }
.rel { border:1px solid #26303b; border-radius:8px; margin-bottom:14px; background:#0d1117; }
.rel h3 { margin:0; padding:12px 16px; border-bottom:1px solid #1d2632; font-size:14px; }
.rel .body { padding:12px 16px; }
h4 { margin:14px 0 6px; font-size:12px; letter-spacing:.08em; color:#7fd8ff;
  text-transform:uppercase; }
ul { margin:4px 0 0; padding-left:18px; }
li { margin:2px 0; }
code { background:#161b22; padding:1px 5px; border-radius:3px; font-size:12px; color:#c9d1d9; }
.note { color:#8b98a5; font-size:12px; margin-top:10px; white-space:pre-wrap; }
.warn { color:#ffc233; }
.ok { color:#3dffa2; }
footer { padding:24px 20px; color:#5d6b7a; font-size:11px; text-align:center; }
"""


def _esc(text) -> str:
    return html.escape(str(text or ""))


def projects() -> list[Path]:
    """Every onboarded project, newest release first (state mtime as the tiebreak)."""
    found: list[Path] = []
    seen: set[Path] = set()
    for base in PROJECTS_BASES:
        if not base or not base.is_dir():
            continue
        for cand in sorted(base.iterdir()):
            if not (cand / ".pipeline" / "profile.json").is_file() or not (cand / ".git").exists():
                continue
            resolved = cand.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(cand)
    return found


def _tags(project: Path, limit: int = TAGS_PER_APP) -> list[str]:
    r = git(["tag", "--sort=-creatordate"], cwd=project, timeout=30)
    tags = [t.strip() for t in (r.stdout or "").splitlines() if t.strip()]
    tags = [t for t in tags if re.match(r"^v?\d", t)]
    return tags[:limit]


def _git(project: Path, *args: str) -> str:
    return git(list(args), cwd=project, timeout=60).stdout or ""


def _changed_files(project: Path, tag: str, prev: str | None) -> list[str]:
    if prev:
        out = _git(project, "diff", "--name-only", f"{prev}..{tag}")
    else:
        out = _git(project, "show", "--name-only", "--format=", tag)
    return [f for f in out.splitlines() if f.strip()]


def _areas(files: list[str]) -> list[str]:
    """Surfaces in a stable order: what a human walks first (screens) before plumbing."""
    found: list[tuple[int, str]] = []
    for f in files:
        for idx, (rx, label) in enumerate(AREA_RULES):
            if re.search(rx, f):
                if all(label != lab for _, lab in found):
                    found.append((idx, label))
                break
    return [label for _, label in sorted(found)]


def _build_file_diff(project: Path, tag: str, prev: str | None) -> str:
    """The version file's diff, so 'the build changed' can mean something real.

    Every release edits versionName/versionCode by definition; flagging 'review the
    build file' on all of them trains the reader to ignore the flag. Only a diff
    that touches something OTHER than the version lines is worth a line.
    """
    if not prev:
        return ""
    try:
        return _git(project, "diff", f"{prev}..{tag}", "--", "**/build.gradle*",
                    "build.gradle*")
    except Exception:
        return ""


def _version_only(diff: str) -> bool:
    changed = [ln for ln in diff.splitlines()
               if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))]
    if not changed:
        return True
    return all(re.search(r"version(Name|Code)", ln) for ln in changed)


def _what_to_test(files: list[str], build_diff: str = "") -> list[str]:
    """Derived, never invented: each line names something the diff can affect."""
    items: list[str] = []
    screens = sorted({m.group(1) for f in files if (m := SCREEN_RX.search(f))})
    if screens:
        items.append("Open " + ", ".join(screens) + " — changed in this release.")
    if any(re.search(r"/calc/", f) for f in files):
        items.append("Enter a failing and a passing reading; the verdict and the "
                     "defect note must match what you typed.")
    if any(re.search(r"/ui/theme/", f) for f in files):
        items.append("Flip the sunlight toggle and walk one screen of each kind.")
    if any(re.search(r"/data/", f) or f.endswith("AppDatabase.kt") for f in files):
        items.append("Install OVER the previous build and confirm your saved jobs survive.")
    if any(f.endswith("AndroidManifest.xml") for f in files):
        items.append("Cold start, and check any new permission prompt.")
    if any("build.gradle" in f for f in files) and not _version_only(build_diff):
        items.append("The build file changed beyond the version stamp (signing, SDK or "
                     "dependencies) — confirm the store build installs cleanly.")
    if any("/pdf/" in f for f in files):
        items.append("Generate a certificate and open the PDF.")
    items.append("Settings → Installed Build shows this version.")
    return items


def _proven_by(files: list[str]) -> list[str]:
    tests = sorted({m.group(1) for f in files if (m := TEST_RX.search(f))})
    return tests


def _risk(files: list[str], build_diff: str = "") -> list[str]:
    flags: list[str] = []
    if any("AndroidManifest.xml" in f for f in files):
        flags.append("manifest (permissions, providers)")
    if any("build.gradle" in f for f in files) and not _version_only(build_diff):
        flags.append("build file (signing, SDK, dependencies)")
    if any(f.endswith("AppDatabase.kt") for f in files):
        flags.append("storage schema (an upgrade path, not a fresh install)")
    if any(f.startswith(".github/") for f in files):
        flags.append("CI")
    return flags


def _handover(project: Path, version: str) -> dict[str, str]:
    """The owner checklist + store note the release step already wrote."""
    out: dict[str, str] = {}
    for f in sorted((project / ".pipeline" / "runs").glob(f"*-handover-v{version}.md")):
        text = f.read_text(errors="ignore")
        for key in ("confirm", "walk", "how"):
            m = re.search(rf"^\s*{key}:\s*(.+)$", text, re.M)
            if m:
                out[key] = m.group(1).strip()
        m = re.search(r"RELEASE CANDIDATE\?\s*(.+)", text)
        if m:
            out["rc"] = m.group(1).strip()
    return out


def _release_cards(project: Path) -> list[dict]:
    profile = profile_of(project)
    state = state_of(project)
    tags = _tags(project)
    rc_calls = {}
    for call in state.get("release_candidates") or []:
        rc_calls[str(call.get("version") or "").lstrip("v")] = call

    cards: list[dict] = []
    for idx, tag in enumerate(tags):
        prev = tags[idx + 1] if idx + 1 < len(tags) else None
        version = tag.lstrip("v")
        files = _changed_files(project, tag, prev)
        commit = _git(project, "rev-list", "-n", "1", tag).strip()
        subject = _git(project, "log", "-1", "--format=%s", tag).strip()
        when = _git(project, "log", "-1", "--format=%cs", tag).strip()
        call = rc_calls.get(version) or {}
        store_version = next((s for s in state.get("shipped") or []
                              if str(s.get("version") or "").lstrip("v") == version), {})
        build_diff = _build_file_diff(project, tag, prev)
        cards.append({
            "version": version,
            "tag": tag,
            "prev": prev,
            "commit": commit[:8],
            "subject": subject,
            "date": when,
            "files": files,
            "areas": _areas(files),
            "to_test": _what_to_test(files, build_diff),
            "proven": _proven_by(files),
            "risk": _risk(files, build_diff),
            "rc": call.get("rc"),
            "rc_why": call.get("why") or call.get("note") or "",
            "code": store_version.get("version_code"),
            "label": store_version.get("label", ""),
            "handover": _handover(project, version),
        })
    return cards


def _app_summary(project: Path) -> dict:
    profile = profile_of(project)
    state = state_of(project)
    cards = _release_cards(project)
    latest = cards[0] if cards else None
    return {
        "name": profile.get("name") or project.name,
        "slug": re.sub(r"[^a-z0-9]+", "-", (profile.get("name") or project.name).lower()).strip("-"),
        "path": str(project),
        "repo": profile.get("repo_slug", ""),
        "latest": latest,
        "releases": len(cards),
        "cards": cards,
        "queue": len(state.get("queue") or []),
        "last_run": (state.get("last_run") or {}).get("result", ""),
        "generated": time.strftime("%Y-%m-%d %H:%M"),
    }


def _badge(card: dict) -> str:
    if card.get("rc") is True:
        return '<span class="badge rc">RELEASE CANDIDATE</span>'
    if card.get("rc") is False:
        return '<span class="badge no">NOT AN RC</span>'
    return '<span class="badge final">RC NOT CALLED</span>'


def _card_html(card: dict) -> str:
    parts = [f'<div class="rel" id="v{_esc(card["version"])}">']
    parts.append(
        f'<h3><span class="ver">{_esc(card["version"])}</span> '
        f'{_badge(card)} <span class="meta">· {_esc(card["date"])} · '
        f'<code>{_esc(card["commit"])}</code>'
        + (f' · versionCode {_esc(card["code"])}' if card.get("code") else "")
        + "</span></h3>"
    )
    parts.append('<div class="body">')
    if card.get("label"):
        parts.append(f'<div><strong>{_esc(card["label"])}</strong></div>')
    if card.get("rc_why"):
        parts.append(f'<div class="meta">rc call: {_esc(card["rc_why"])}</div>')

    parts.append("<h4>What changed</h4><ul>")
    if card["areas"]:
        for area in card["areas"]:
            parts.append(f"<li>{_esc(area)}</li>")
    else:
        parts.append("<li>no file changes recorded for this tag</li>")
    n = len(card["files"])
    parts.append(f'</ul><div class="meta">{n} file(s)'
                 + (f' vs {_esc(card["prev"])}' if card.get("prev") else " (first release)")
                 + "</div>")

    parts.append("<h4>What to test</h4><ul>")
    for item in card["to_test"]:
        parts.append(f"<li>{_esc(item)}</li>")
    parts.append("</ul>")

    if card["proven"]:
        parts.append("<h4>Proven by</h4><ul>")
        for t in card["proven"]:
            parts.append(f"<li><code>{_esc(t)}</code></li>")
        parts.append("</ul>")

    if card["risk"]:
        parts.append('<h4 class="warn">Review the diff for</h4><ul>'
                     + "".join(f"<li>{_esc(r)}</li>" for r in card["risk"]) + "</ul>")

    if card["handover"].get("confirm"):
        parts.append(f'<div class="note">owner check: {_esc(card["handover"]["confirm"])}</div>')
    parts.append("</div></div>")
    return "".join(parts)


def render_index(apps: list[dict]) -> str:
    rows = []
    for app in apps:
        latest = app["latest"] or {}
        rc = latest.get("rc")
        badge = ("<span class='badge rc'>RC</span>" if rc is True
                 else "<span class='badge no'>not rc</span>" if rc is False
                 else "<span class='badge final'>—</span>")
        rows.append(f"""
<a class="app" href="{_esc(app['slug'])}.html">
  <div class="row">
    <span class="name">{_esc(app['name'])} {badge}</span>
    <span class="ver">v{_esc(latest.get('version', '—'))}</span>
  </div>
  <div class="row meta">
    <span>{_esc(latest.get('label') or latest.get('subject') or '')}</span>
    <span>{_esc(latest.get('date', ''))} · {app['releases']} release(s) · queue {app['queue']}</span>
  </div>
</a>""")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>dev-team — what shipped, what to test</title><style>{CSS}</style></head><body>
<header><h1>WHAT SHIPPED · WHAT TO TEST</h1>
<div class="sub">every app this machine ships · built {_esc(time.strftime('%Y-%m-%d %H:%M'))} · tailnet only</div></header>
<main>{''.join(rows) or '<p class="meta">no onboarded projects found</p>'}</main>
<footer>generated from each project's release ledger, tags and diffs · never hand-edited</footer>
</body></html>"""


def render_app(app: dict) -> str:
    cards = "".join(_card_html(c) for c in app["cards"]) or '<p class="meta">no release tags yet</p>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(app['name'])} — releases</title><style>{CSS}</style></head><body>
<header><h1>{_esc(app['name'])}</h1>
<div class="sub">{_esc(app['repo'])} · {app['releases']} shown · queue {app['queue']} ·
<a href="index.html">all apps</a></div></header>
<main>{cards}</main>
<footer>what changed is the tag diff · what to test is derived from the files it touched</footer>
</body></html>"""


def build() -> dict:
    """Regenerate the whole site. Returns what was written (for the caller to print)."""
    apps = [_app_summary(p) for p in projects()]
    apps.sort(key=lambda a: (a["latest"] or {}).get("date", ""), reverse=True)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    index = SITE_DIR / "index.html"
    index.write_text(render_index(apps))
    written.append(index)
    for app in apps:
        page = SITE_DIR / f"{app['slug']}.html"
        page.write_text(render_app(app))
        written.append(page)
    stamp = {"built": time.strftime("%Y-%m-%dT%H:%M:%S"), "apps": len(apps)}
    (SITE_DIR / "build.json").write_text(json.dumps(stamp, indent=2))
    return {"built": stamp["built"], "apps": [a["name"] for a in apps],
            "files": len(written), "dir": str(SITE_DIR)}


def status() -> dict:
    """Is the page current? Freshness is a FACT to check, not a habit to remember."""
    stamp_file = SITE_DIR / "build.json"
    if not stamp_file.is_file():
        return {"built": None, "stale": [], "reason": "never built"}
    built = json.loads(stamp_file.read_text()).get("built", "")
    stale = []
    for p in projects():
        state = p / ".pipeline" / "state.json"
        if state.is_file() and time.strftime("%Y-%m-%dT%H:%M:%S",
                                             time.localtime(state.stat().st_mtime)) > built:
            stale.append(p.name)
    return {"built": built, "stale": stale}
