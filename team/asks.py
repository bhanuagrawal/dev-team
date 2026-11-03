"""Deterministic review of owner asks: can a machine do this instead of the owner?

The pipeline may ask the owner only what no agent can DECIDE. A CHORE dressed as
an owner ask — "enable this API in the console", "create that product", "grant
this permission" — is a defect: the owner's own signed-in browser (browser-control)
or a native desktop tool (desktop-use) can do it, and asking is how the owner ends
up doing an agent's work.

This module is the deterministic judge of that question. It is a RULE TABLE, not
a model: no network, no LLM, and no project or app names (the team must carry
none). Each rule matches the TEXT of an ask (its title plus its `how`/`benefit`)
and answers with three things:

  * the TOOL that can do it (and whether this machine actually has it),
  * the ordered STEPS that tool should take,
  * the one thing the owner genuinely still has to supply — a card, a legal
    answer, a 2FA tap — because those are the only parts that really are theirs.

Determinism is the point: the same ask text on the same machine always yields the
same verdict, `evidence` names the binary or file that decided tool availability,
and a declaration the profile ALREADY holds is caught before any tool is dragged
in (the ask is then a read, not a chore).

Verdicts:
  agent  — a tool can do this end to end; it must never reach the owner as a chore
  mixed  — the agent drives, the owner supplies one named thing at one point
  owner  — no machine path: a policy act (production promotion), a payment
           instrument, or text no rule recognises (reported as such, never guessed)
"""
from __future__ import annotations

import re
import shutil
import textwrap
from pathlib import Path

# --------------------------------------------------------------------------- #
# what this machine can actually drive
# --------------------------------------------------------------------------- #
# Availability is EVIDENCE, not a wish: a PATH binary, a harness config that
# declares the server, or the agent skill that fronts it. A tool that is absent is
# still named in the verdict (the ask stays agent-work — it just needs the tool
# started), and the evidence line says what was missing.
TOOLS: dict[str, dict] = {
    "browser-control": {
        "label": "browser-control (the owner's own logged-in Chromium)",
        "binaries": ("browser-control",),
        "config_hints": ("browser-control",),
        "skill_dirs": ("browser-control",),
        "run": "browser-control execute '<playwright steps above>'",
    },
    "desktop-use": {
        "label": "desktop-use (native macOS windows, menus and system dialogs)",
        "binaries": ("peekaboo", "desktop-use", "cliclick"),
        "config_hints": ('"desktop"', "desktop-use", "peekaboo"),
        "skill_dirs": ("desktop-use", "screen-router"),
        "run": "route it to the desktop-use agent (tools.desktop MCP)",
    },
}

_HARNESS_CONFIGS = (
    ".config/opencode/opencode.jsonc",
    ".config/opencode/opencode.json",
    ".opencode/opencode.json",
    ".codex/config.json",
)


def probe_tools(home: Path | None = None) -> dict[str, dict]:
    """Which MCP/browser/desktop drivers this machine really has, with evidence.

    Pure filesystem + PATH inspection: no network, no process spawn, stable across
    machines. `evidence` is always filled — "why do you think you can do this?"
    must be answerable from the report alone.
    """
    home = Path(home or Path.home())
    texts: list[str] = []
    for rel in _HARNESS_CONFIGS:
        try:
            texts.append((home / rel).read_text(errors="ignore"))
        except OSError:
            continue
    out: dict[str, dict] = {}
    for tid, spec in TOOLS.items():
        evidence = ""
        for binary in spec["binaries"]:
            found = shutil.which(binary)
            if found:
                evidence = f"{binary} at {found}"
                break
        if not evidence:
            for hint in spec["config_hints"]:
                if any(hint in t for t in texts):
                    evidence = f"{hint.strip(chr(34))} declared in a harness config"
                    break
        if not evidence:
            for name in spec["skill_dirs"]:
                d = home / ".agents" / "skills" / name
                if d.is_dir():
                    evidence = f"skill {d.name} installed at {d}"
                    break
        out[tid] = {
            "id": tid,
            "label": spec["label"],
            "available": bool(evidence),
            "evidence": evidence or (
                "not detected on this machine (no binary, harness config entry or "
                "agent skill) — the work is still an agent's; the tool must be started"
            ),
            "run": spec["run"],
        }
    return out


# --------------------------------------------------------------------------- #
# asks that stay the owner's — checked FIRST, so a console word cannot override
# --------------------------------------------------------------------------- #
VETO: list[dict] = [
    {
        "id": "production-promotion",
        "match": (r"\bpromot\w*\b.{0,40}\bproduction\b|"
                  r"\bproduction\b.{0,40}\bpromot\w*\b|\bpublic listing\b"),
        "why": ("promoting beyond internal testing is human-only policy: the team "
                "never promotes and never asks about it — report it as a fact"),
    },
    {
        "id": "payment-instrument",
        "match": r"\bcredit card\b|\bpayment method\b|\bcard on file\b|\bbilling account\b",
        "why": "a payment instrument is the owner's; no agent types a card",
    },
]

# --------------------------------------------------------------------------- #
# things only the owner can SUPPLY while a tool drives
# --------------------------------------------------------------------------- #
OWNER_INPUT: list[tuple[str, str]] = [
    (r"\b2fa\b|two-factor|verification code|passkey|captcha",
     "approve the sign-in / 2FA / CAPTCHA prompt when it appears (the agent drives, you tap)"),
    (r"application-default login|gcloud auth login|one[- ]time .{0,20}sign[- ]?in",
     "a one-time sign-in as you (the account consent is yours; the agent starts the flow)"),
    (r"\bprice\b|\bpricing\b|\$\s?\d",
     "the price the item should carry"),
    (r"content rating|data safety|declaration form|\blegal\b|terms of service",
     "the factual / legal answers the form asks for"),
    (r"\btax\b|\bbank\b|\bidentity\b|date of birth",
     "your identity / tax / bank details — never typed by an agent"),
]

# A decision the ask itself spells out (which property, which account, approve or
# not) is the owner's to make, even when the tooling is obvious. Deliberately
# narrow: the bare verb "approve" in a title ("Approve screen recording") is the
# ACTION, not a decision, and must not turn a mechanical task into an owner ask.
_DECISION = (r"\bwhich\b|\bchoose\b|\bdecide\b|\bconfirm you want\b|\byour approval\b",
             "the choice the ask names (the agent cannot make it for you)")

# --------------------------------------------------------------------------- #
# tool rules — most specific first; the first match wins
# --------------------------------------------------------------------------- #
RULES: list[dict] = [
    {
        "id": "play-console",
        "tool": "browser-control",
        "site": "Play Console",
        "match": (r"play console|google play|internal testing|testing track|"
                  r"store listing|store presence|managed publishing|app content|"
                  r"data safety|content rating|users? and permissions|"
                  r"in-app product|\biap\b|product id|subscription"),
        "pages": [
            (r"users? and permissions|permission|grant\b|access\b",
             "Users and permissions → the service account → App permissions"),
            (r"in-app product|\biap\b|product id|subscription|price",
             "Monetize → Products → In-app products"),
            (r"managed publishing|publishing overview",
             "Publishing overview → Changes ready to publish"),
            (r"store listing|store presence|screenshot|feature graphic|icon",
             "Grow → Store presence → Main store listing"),
            (r"internal testing|testing track|testers", "Test and release → Testing"),
            (r"data safety|content rating|app content|declaration",
             "Policy → App content"),
        ],
        "default_page": "the app's dashboard for the surface the ask names",
        "action": ("make the change with the console's own save / publish control "
                   "(an upload is not a publish)"),
    },
    {
        "id": "firebase-analytics",
        "tool": "browser-control",
        "site": "Firebase console / Google Analytics",
        "match": (r"firebase|ga4|analytics property|data stream|"
                  r"google-services\.json|register .{0,20}sha|analytics\.google\.com"),
        "pages": [
            (r"ga4|analytics property|data stream|property id|analytics\.google\.com",
             "analytics.google.com → Admin → Property → Data streams"),
            (r"google-services\.json|register .{0,20}sha|firebase console|firebase project",
             "Firebase console → Project settings → Your apps (add app / SHA / download config)"),
        ],
        "default_page": "the property / project the ask names",
        "action": "create or link the item, then copy the identifier the ask needs",
    },
    {
        "id": "google-cloud",
        "tool": "browser-control",
        "site": "Google Cloud console",
        # `www.googleapis.com` is an OAuth scope host, not an API to enable — the
        # lookbehind keeps a sign-in scope URL from masquerading as a console chore.
        "match": (r"cloud console|google cloud|apis? & services|enable .{0,40}\bapi\b|"
                  r"\biam\b|service account|oauth consent|credentials page|"
                  r"developerreporting|(?<!www\.)[a-z0-9-]+\.googleapis\.com"),
        "pages": [
            (r"enable .{0,40}\bapi\b|apis? & services|developerreporting|googleapis\.com",
             "APIs & Services → Library → the named API → Enable"),
            (r"service account|credentials|key_file|\bsa\b",
             "IAM & Admin → Service Accounts → the account → Keys"),
            (r"\biam\b|grant|role", "IAM & Admin → IAM → grant the named role"),
        ],
        "default_page": "the project's dashboard",
        "action": "apply the change and wait for the API/enablement to report itself on",
    },
    {
        "id": "macos-settings",
        "tool": "desktop-use",
        "site": "System Settings",
        "match": (r"system settings|system preferences|privacy & security|"
                  r"screen recording|accessibility permission|full disk access|"
                  r"login item|notification permission|keychain access"),
        "pages": [
            (r"screen recording|accessibility|full disk access|privacy & security"
             r"|notification permission",
             "Privacy & Security → the named permission → the app"),
            (r"login item|startup|background item",
             "General → Login Items & Extensions"),
        ],
        "default_page": "the pane the ask names",
        "action": "toggle the setting and let the OS apply it (a prompt may require your password / Touch ID)",
    },
    {
        "id": "native-app-dialog",
        "tool": "desktop-use",
        "site": "the app's own window",
        "match": (r"xcode|android studio (?:app|wizard|dialog)|avd manager|"
                  r"simulator (?:window|menu)|crash dialog|consent dialog"),
        "pages": [],
        "default_page": "the dialog the ask names",
        "action": "press the dialog's own control and read the result back from the window",
    },
]


def _first(items, text: str, key: str = "match"):
    for it in items:
        if re.search(it[key], text, re.I):
            return it
    return None


def _has_profile_path(profile: dict, dotted: str) -> bool:
    node = profile
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return bool(node)


def _page(rule: dict, text: str) -> str:
    for pattern, label in rule.get("pages") or []:
        if re.search(pattern, text, re.I):
            return label
    return rule.get("default_page") or "the surface the ask names"


def _steps(rule: dict, text: str) -> list[str]:
    page = _page(rule, text)
    if rule["tool"] == "browser-control":
        return [
            f"open {rule['site']} in the owner's own browser session (browser-control "
            "drives the tab that is already signed in — never a fresh profile)",
            f"walk to: {page}",
            rule["action"],
            "read the change back from the page (never call it done from the click)",
        ]
    return [
        f"open {rule['site']} with the desktop driver (desktop-use reads the real "
        "window, not a screenshot guess)",
        f"walk to: {page}",
        rule["action"],
        "read the setting back from the screen and say what it now shows",
    ]


def review(title: str, how: str = "", profile: dict | None = None,
           tools: dict | None = None) -> dict:
    """Classify ONE ask. Pure: same text + same machine → same verdict."""
    profile = profile or {}
    tools = tools or probe_tools()
    text = f"{title or ''}\n{how or ''}"

    row: dict = {"title": (title or "?").strip(), "source_text": (how or "").strip(),
                 "verdict": "owner", "tool": None, "tool_label": "",
                 "available": None, "evidence": "", "steps": [],
                 "owner_input": [], "why": "", "run": ""}

    veto = _first(VETO, text)
    if veto:
        row["why"] = veto["why"]
        return row

    # A connection the profile ALREADY declares is not a chore and not a console
    # trip: it is a read the pipeline owes itself. Caught before any tool is named.
    m = re.search(r"declare\s+profile\.([A-Za-z0-9_.]+)", text)
    if m:
        path = m.group(1).rstrip(".")
        if _has_profile_path(profile, path):
            row.update(
                verdict="agent", tool=None,
                steps=[f"profile.{path} is already declared — the missing piece is the "
                       "READ, not an owner chore",
                       "run the collector (`devteam research <project>`) and re-check the "
                       "flag; a presence-driven flag clears itself when the read answers"],
                why=f"profile.{path} exists, so nothing has to be created or granted",
            )
            return row

    rule = _first(RULES, text)
    if not rule:
        row["why"] = ("no console page, cloud API, OS setting or app dialog is named in "
                      "the ask — nothing a browser or desktop tool can drive")
        return row

    meta = tools.get(rule["tool"]) or {"available": False, "evidence": "not detected",
                                       "label": rule["tool"], "run": ""}
    owner_input = [note for pattern, note in OWNER_INPUT
                   if re.search(pattern, text, re.I)]
    if rule["id"] == "firebase-analytics":
        owner_input.append("which analytics property is the app's (or approval to create one)")
    if re.search(_DECISION[0], text, re.I):
        owner_input.append(_DECISION[1])

    # De-duplicate while keeping the rule order deterministic.
    seen: set[str] = set()
    owner_input = [x for x in owner_input if not (x in seen or seen.add(x))]

    row.update(
        verdict="mixed" if owner_input else "agent",
        tool=rule["tool"], tool_label=meta["label"],
        available=bool(meta["available"]), evidence=str(meta.get("evidence") or ""),
        steps=_steps(rule, text), owner_input=owner_input,
        why=f"the ask names {rule['site']} — a surface a machine can drive",
        run=str(meta.get("run") or ""),
    )
    if re.search(r"declare|declared|profile\.", text, re.I):
        row["steps"].append(
            "write the identifier the console showed into the project profile "
            "(`devteam adapt <project>` verifies it — that edit is agent work too)")
    return row


# --------------------------------------------------------------------------- #
# gathering the open items (asks + agent flags) from a project
# --------------------------------------------------------------------------- #
def open_items(project: Path, flags: list[dict] | None = None) -> list[dict]:
    """Every open ask/flag as {source, title, how, benefit} — one loader for the
    banner, `doctor` and the `asks` command, so they can never disagree.

    `flags` lets a caller hand in the same refreshed upgrade list its banner
    prints (cli refreshes the store-readiness ones); without it the state/research
    copy is used.
    """
    from .core import profile_of, state_of
    from . import goals

    profile = profile_of(project)
    state = state_of(project)
    rows: list[dict] = []
    for a in goals.owner_asks(profile, state, project.name):
        rows.append({"source": "owner_ask", "title": a.get("what") or "?",
                     "how": a.get("how") or "", "benefit": ""})
    if flags is None:
        flags = state.get("upgrades") or []
        if not flags:
            try:
                import json
                flags = json.loads((Path(project) / ".pipeline" / "research.json")
                                   .read_text()).get("upgrades", [])
            except Exception:
                flags = []
    for u in flags:
        rows.append({"source": "agent_flag",
                     "title": u.get("title") or u.get("id") or "?",
                     "how": u.get("how") or "", "benefit": u.get("benefit") or ""})
    return rows


def review_project(project: Path, flags: list[dict] | None = None) -> list[dict]:
    from .core import profile_of
    profile = profile_of(project)
    tools = probe_tools()
    out = []
    for item in open_items(project, flags=flags):
        row = review(item["title"], item["how"], profile=profile, tools=tools)
        row["source"] = item["source"]
        row["benefit"] = item["benefit"]
        out.append(row)
    return out


# --------------------------------------------------------------------------- #
# rendering — the block an agent quotes, and the payload a script reads
# --------------------------------------------------------------------------- #
_VERDICT_LABEL = {
    "agent": "AGENT — a tool can do this end to end; do not hand it to the owner",
    "mixed": "MIXED — the agent drives, the owner supplies one named thing",
    "owner": "OWNER — no machine path",
}


def _wrap(label: str, text: str, indent: int = 4) -> list[str]:
    pad = " " * indent
    return [pad + line for line in textwrap.wrap(f"{label}{text}",
                                                 width=96,
                                                 subsequent_indent=pad)]


def render(rows: list[dict], heading: str = "OWNER ASK REVIEW") -> str:
    if not rows:
        return f"--- {heading} ---\n  (no open asks or flags — nothing to review)"
    out = [f"--- {heading} (deterministic: rule table, no model) ---"]
    for row in rows:
        tag = "?" if row.get("source", "owner_ask") == "owner_ask" else "·"
        out.append(f"  {tag} {row['title']}")
        out.append(f"    verdict: {_VERDICT_LABEL[row['verdict']]}")
        if row["verdict"] == "owner":
            out.extend(_wrap("why: ", row["why"] + "."))
            continue
        out.append(f"    tool:    {row['tool_label']} — "
                   + ("available (" + row["evidence"] + ")"
                      if row["available"] else row["evidence"]))
        if row["steps"]:
            out.append("    plan:")
            for i, step in enumerate(row["steps"], 1):
                out.extend(_wrap(f"{i}) ", step, indent=6))
        if row["owner_input"]:
            for note in row["owner_input"]:
                out.extend(_wrap("owner supplies: ", note))
        else:
            out.append("    owner supplies: nothing — the agent can do this end to end")
    agentish = sum(1 for r in rows if r["verdict"] in ("agent", "mixed"))
    out.append(f"  summary: {len(rows)} open · {agentish} a machine can drive"
               f" · {len(rows) - agentish} owner-only")
    return "\n".join(out)


def summary_line(rows: list[dict]) -> str:
    """One line for doctor/alarm: how many open asks are actually agent work."""
    agentish = [r for r in rows if r["verdict"] in ("agent", "mixed")]
    if not agentish:
        return f"asks: {len(rows)} open, none machine-resolvable (all genuinely the owner's)"
    tools = sorted({r["tool"] for r in agentish if r.get("tool")})
    return (f"asks: {len(agentish)} of {len(rows)} open item(s) can be done by an agent "
            f"({', '.join(tools) or 'no tool detected'}) — do not hand them to the owner; "
            f"run `devteam asks <project>` for the plan")


# --------------------------------------------------------------------------- #
# the rule table is code, so it is checkable: a fixed table of representative
# asks, each with the verdict it MUST produce. `devteam asks <project> --selftest`
# runs it, so a rule edit that starts mis-filing real work is caught here rather
# than by an owner being handed a chore.
# --------------------------------------------------------------------------- #
SELFTEST: list[tuple[str, str, dict | None, str, str | None]] = [
    ("Enable playdeveloperreporting.googleapis.com on the upload project and grant "
     "'View app information' in Play Console",
     "", None, "agent", "browser-control"),
    ("Create the in-app product for the paid feature", "the product should carry a $3.99 price",
     None, "mixed", "browser-control"),
    ("Connect app analytics", "declare profile.connections.ga4 {property: <GA4 property id>}",
     None, "mixed", "browser-control"),
    ("Approve screen recording", "System Settings → Privacy & Security → Screen Recording → the app",
     None, "agent", "desktop-use"),
    ("Ship it", "promote the release to production when it looks good", None, "owner", None),
    ("Add a payment method", "put a credit card on the account", None, "owner", None),
    ("Find out why the nightly loop stopped", "no console surface is configured",
     None, "owner", None),
    # A declaration the profile already holds is a read, not a console trip.
    ("Connect app analytics", "declare profile.connections.ga4 {property: <GA4 property id>}",
     {"connections": {"ga4": {"property": "123"}}}, "agent", None),
]


def selftest() -> list[str]:
    """Run the table; return human-readable failures (empty = the table holds)."""
    failures: list[str] = []
    for title, how, profile, want_verdict, want_tool in SELFTEST:
        row = review(title, how, profile=profile or {})
        if row["verdict"] != want_verdict or row["tool"] != want_tool:
            failures.append(
                f"{title!r}: got verdict={row['verdict']} tool={row['tool']}, "
                f"want verdict={want_verdict} tool={want_tool}")
    return failures
