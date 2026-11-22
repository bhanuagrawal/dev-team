"""ANALYTICS — does the app record what the PLANNER needs to decide?

Why this exists (2026-11-22): every managed app declared `connections.ga4` and
passed `doctor`, because the only analytics gate was "does some source file
contain the string `logEvent`" — which a single no-op call satisfies. The
planner then read a dashboard whose every number was about installs, not about
the product: one app showed 715 first opens and a single completed core job,
another showed hundreds of matches started and no purchase, and a launcher whose
whole premise is "find what people will pay for" had no paywall event at all.
A number the app never recorded is not a gap in the report — it is a decision
taken blind.

So the contract is DECLARED per project (`profile['analytics']`) and then
CHECKED against two independent facts:

  * the delivery tree  — does the code name the event at all, and does something
                          actually PASS that name to a send?
  * the GA4 property   — did a real user record it in the last 30 days?

The second half of the first fact was added the day the audit ran: one app
declared its two core events as constants and no screen ever sent them, so "the
string is in the tree" certified two events the product never emitted — and the
property's zeros read as "nobody uses that part of the product".

A declared event that never lands is worse than an undeclared one: the plan
reads its zero as "nobody does this" when the truth is "the app never said it".

Mode `none` is a first-class answer: an app whose product law forbids analytics
(one app's P2) declares `{"mode": "none", "law": "P2"}` and the gate then
checks the ABSENCE — no SDK, no tracker, no GA4/Firebase connection — instead of
demanding events it must never send.

Consumers: `devteam analytics <project>` prints the table, `doctor` turns each
blocked row into a BLOCKED gate, `research` turns each gap into a queue upgrade,
and `adapt` drafts a declaration for a new app from its live events.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .core import DELIVERY_REF, git, profile_of

DAYS = 30

# The classes a measured product must answer, in the order a planner reads
# them. `money` is required only when the tree carries a billing path — an app
# that sells nothing owes no purchase funnel.
CLASSES: tuple[tuple[str, str, str], ...] = (
    ("activation", "the core job is started",
     "the denominator every funnel and every priority is read from"),
    ("completion", "the core job finishes",
     "a start with no finish is the drop-off the next release should fix"),
    ("features", "each optional surface records itself",
     "which part of the product earns its keep, and which is dead weight"),
    ("money", "the paywall is shown and the purchase is recorded",
     "what share of users will pay — the only number a subscription business runs on"),
)
REQUIRED = ("activation", "completion", "features")
CLASS_TITLES = {c: t for c, t, _ in CLASSES}
CLASS_WHY = {c: w for c, _, w in CLASSES}

# A completion event that reaches under this fraction of the activations is a
# finding, not a verdict: the planner is told the drop-off, never told to fix it
# from a ratio alone.
DROPOFF_ALERT = 0.20
DROPOFF_MIN_USERS = 10

_SRC_EXT = (".kt", ".java", ".dart", ".swift")
_EVENT_RE = re.compile(r'"([a-z][a-z0-9_]{3,40})"')
_SDK_RE = re.compile(r"firebase-analytics|firebase\.analytics|play-services-analytics|"
                     r"libs\.firebase\.analytics|com\.google\.firebase")
_TRACKER_RE = re.compile(r"logEvent|AnalyticsTracker|AnalyticsEvent|FirebaseAnalytics")
_BILLING_RE = re.compile(r"BillingClient|BillingRepository|AndroidBillingProvider|"
                         r"in_app_purchase|revenuecat|StoreKit|Paywall|paywall")

# A clause that says nothing may leave the phone is a clause a `measured` app
# cannot be checked against — the two facts would contradict before the first
# report is read. Kept to the phrases an app actually declares (not "trackers",
# not "analytics" on its own: an app may PERMIT anonymous usage analytics).
_SILENT_LAW_RE = re.compile(
    r"\bno\s+telemetry\b|\bsends\s+nothing\b|\bno\s+analytics\b|"
    r"\bnothing\b[^.]{0,80}\bleaves\s+the\s+device\b", re.I)


# --------------------------------------------------------------------------- #
# the declaration
# --------------------------------------------------------------------------- #
def declared(profile: dict) -> dict:
    """profile['analytics'], normalised enough for the checks to read it."""
    raw = profile.get("analytics")
    return raw if isinstance(raw, dict) else {}


def mode(profile: dict) -> str:
    m = str(declared(profile).get("mode") or "").strip().lower()
    return m if m in ("measured", "none") else ""


def declared_names(decl: dict) -> dict[str, list[str]]:
    """class -> event names, with money flattened to [shown..., success...]."""
    out: dict[str, list[str]] = {}
    classes = decl.get("classes") or {}
    for cid, _, _ in CLASSES:
        val = classes.get(cid)
        if cid == "money" or isinstance(val, dict):
            names: list[str] = []
            if isinstance(val, dict):
                for half in ("shown", "success", "cancel"):
                    names += [str(n) for n in (val.get(half) or []) if str(n).strip()]
            out[cid] = names
        elif isinstance(val, list):
            out[cid] = [str(n).strip() for n in val if str(n).strip()]
        else:
            out[cid] = []
    return out


def money_halves(decl: dict) -> dict[str, list[str]]:
    val = (decl.get("classes") or {}).get("money")
    if not isinstance(val, dict):
        return {"shown": [], "success": []}
    return {half: [str(n) for n in (val.get(half) or []) if str(n).strip()]
            for half in ("shown", "success")}


def draft(profile: dict, events: dict | None) -> dict:
    """A declaration an agent can confirm and `--declare`.

    Proposes from the evidence, not from taste: the core classes are named by
    what users already recorded, so a drafted contract is true on the day it is
    written. It is a DRAFT — the agent (or owner) sets the words.
    """
    live = {k: (v or {}).get("users") or 0 for k, v in (events or {}).items()}
    auto = {"first_open", "first_visit", "session_start", "user_engagement",
            "app_update", "app_remove", "screen_view", "app_exception",
            "(not set)", "page_view", "scroll", "click", "form_start",
            "form_submit", "video_start", "video_complete", "app_opened"}
    domain = {k: v for k, v in live.items() if k not in auto and v > 0}
    ranked = sorted(domain.items(), key=lambda kv: -kv[1])

    def pick(pats: tuple[str, ...], limit: int = 3) -> list[str]:
        return [n for n, _ in ranked if any(re.search(p, n) for p in pats)][:limit]

    activation = pick((r"start", r"initiat", r"open", r"begin", r"scan", r"launch"))
    completion = pick((r"complet", r"finish", r"saved", r"success", r"result",
                       r"created", r"done", r"paid"))
    failure = pick((r"fail", r"error", r"cancel"))
    money_shown = pick((r"paywall.*show", r"upgrade.*show", r"purchase.*start"))
    money_ok = pick((r"purchase.*(success|complet)", r"subscri.*(success|convert)",
                     r"upgrade.*(success|complet)", r"paid"))
    return {
        "mode": "measured",
        "why": "drafted from the events GA4 recorded in the last 30 days",
        "classes": {
            "activation": activation,
            "completion": completion or activation[:1],
            "features": [n for n, _ in ranked
                         if n not in set(activation) | set(completion)][:3],
            "money": {"shown": money_shown, "success": money_ok},
        },
        "_evidence": {"activation_users": {n: live.get(n, 0) for n in activation},
                      "completion_users": {n: live.get(n, 0) for n in completion}},
    }


# --------------------------------------------------------------------------- #
# the delivery tree
# --------------------------------------------------------------------------- #
def _tree_files(project: Path) -> list[str]:
    r = git(["ls-tree", "-r", "--name-only", DELIVERY_REF], cwd=project, timeout=60)
    if r.returncode != 0:
        return []
    return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]


def _source_files(project: Path) -> list[str]:
    return [f for f in _tree_files(project) if f.endswith(_SRC_EXT)]


_BUILD_EXT = (".gradle", ".gradle.kts", ".toml", ".pbxproj", ".podspec")


def _read(project: Path, rel: str) -> str:
    r = git(["show", f"{DELIVERY_REF}:{rel}"], cwd=project, timeout=30)
    return r.stdout if r.returncode == 0 else ""


def tree_contains(project: Path, names: list[str]) -> set[str]:
    """Which of `names` appear as a string literal anywhere in the delivered tree.

    Deliberately NOT limited to the analytics files: a feature event may be sent
    through an injected tracker in a screen the tracker module never imports, and
    an event named only in the plan is exactly the failure this check exists to
    catch (a property can still carry events for features the shipped tree no
    longer draws — old installs, not the product).
    """
    names = [n for n in dict.fromkeys(names) if n]
    if not names:
        return set()
    pat = "|".join(re.escape(n) for n in names)
    r = git(["grep", "-h", "-o", "-E", f'"({pat})"', DELIVERY_REF, "--",
             "*.kt", "*.java", "*.dart", "*.swift"], cwd=project, timeout=60)
    if r.returncode not in (0, 1):
        return set()
    found = {ln.strip().strip('"') for ln in (r.stdout or "").splitlines() if ln.strip()}
    return found & set(names)


_DECL_RE = re.compile(r'\b([A-Z][A-Za-z0-9_]{2,})\s*(?::[^=]*)?=\s*"[^"]*"')
_TEST_PATH_RE = re.compile(r"(?:^|/)(?:androidTest|test|androidUnitTest|commonTest|"
                           r"iosTest)/|Test\.kt$|Tests\.kt$", re.I)


def _quoted_lines(project: Path, names: list[str]) -> dict[str, list[tuple[str, str]]]:
    """name -> [(file, line)] for every delivered line that quotes it."""
    names = [n for n in dict.fromkeys(names) if n]
    if not names:
        return {}
    pat = "|".join(re.escape(n) for n in names)
    r = git(["grep", "-n", "-E", f'"({pat})"', DELIVERY_REF, "--",
             "*.kt", "*.java", "*.dart", "*.swift"], cwd=project, timeout=90)
    if r.returncode not in (0, 1):
        return {}
    out: dict[str, list[tuple[str, str]]] = {n: [] for n in names}
    for ln in (r.stdout or "").splitlines():
        # `git grep <tree-ish>` prints ref:path:line:text
        parts = ln.split(":", 3)
        if len(parts) < 4:
            continue
        rel, content = parts[1], parts[3]
        for n in names:
            if f'"{n}"' in content:
                out[n].append((rel, content))
    return out


def emits(project: Path, names: list[str]) -> dict[str, bool]:
    """name -> does the delivered tree PASS it to a call, or only DECLARE it?

    Why this exists (2026-11-22, found by the first audit): one app's tracker
    declared three of its core events as constants and no screen ever sent them,
    so `tree_contains` passed — "the name is in the code" — while those acts
    recorded nothing for 30 days. A constant is a name, not an event.

    Only the shape that can be judged is judged: a constant assigned the literal
    (`const val WISH_CREATED = "wish_created"`) whose identifier is used nowhere
    outside tests. Anything else (a `when` branch mapping a data class, a raw
    `logEvent("x")`) returns True — the check may not guess, and an event it
    cannot judge must not be called dead.
    """
    lines = _quoted_lines(project, names)
    out: dict[str, bool] = {}
    for name, hits in lines.items():
        if not hits:
            out[name] = True
            continue
        ident = ""
        for _, content in hits:
            m = _DECL_RE.search(content)
            if m:
                ident = m.group(1)
                break
        if not ident:
            out[name] = True          # a shape this check does not judge
            continue
        r = git(["grep", "-n", "-w", ident, DELIVERY_REF, "--",
                 "*.kt", "*.java", "*.dart", "*.swift"], cwd=project, timeout=90)
        if r.returncode not in (0, 1):
            out[name] = True
            continue
        uses = 0
        for ln in (r.stdout or "").splitlines():
            parts = ln.split(":", 3)
            rel = parts[1] if len(parts) >= 4 else ""
            if _TEST_PATH_RE.search(rel):
                continue
            uses += 1
        # the declaration line itself is one of those hits
        declared = sum(1 for _, content in hits if _DECL_RE.search(content))
        out[name] = (uses - declared) > 0
    return out


def source_scan(project: Path) -> dict:
    """What the SHIPPED tree says: SDK, tracker seam, billing path, event names.

    Reads the delivery ref, not the checkout — a stale checkout once had the
    team certify a theme users never received. `names` is every snake_case string
    literal in a file that also mentions analytics, which is how both a typed
    event class and a raw `logEvent("x")` call are seen.
    """
    files = _source_files(project)
    if not files and not _tree_files(project):
        return {"ref": DELIVERY_REF, "available": False, "sdk": False,
                "tracker": False, "billing": False, "names": set(), "files": []}
    sdk = tracker = billing = False
    names: set[str] = set()
    hit: list[str] = []
    # the SDK is declared in a BUILD file, never in a screen
    for rel in [f for f in _tree_files(project) if f.endswith(_BUILD_EXT)]:
        if _SDK_RE.search(_read(project, rel)):
            sdk = True
    for rel in files:
        txt = _read(project, rel)
        if not txt:
            continue
        if _SDK_RE.search(txt):
            sdk = True
        if _BILLING_RE.search(txt):
            billing = True
        if _TRACKER_RE.search(txt):
            tracker = True
            hit.append(rel)
            names |= set(_EVENT_RE.findall(txt))
    return {"ref": DELIVERY_REF, "available": True, "sdk": sdk, "tracker": tracker,
            "billing": billing, "names": names, "files": hit}


# --------------------------------------------------------------------------- #
# the property
# --------------------------------------------------------------------------- #
def live_events(project: Path, profile: dict) -> tuple[dict | None, str]:
    """{event: {events, users}} over 30 days, or (None, why-not)."""
    from . import ga4 as ga4_mod
    conf = (profile.get("connections") or {}).get("ga4") or {}
    if not conf.get("property"):
        return None, "connections.ga4.property is not declared"
    return ga4_mod.event_users(project, conf, DAYS)


# --------------------------------------------------------------------------- #
# the audit
# --------------------------------------------------------------------------- #
def _check(cid: str, status: str, detail: str, fix: str = "") -> dict:
    return {"id": cid, "status": status, "detail": detail, "fix": fix}


def audit(project: Path, profile: dict | None = None,
          events: dict | None = None, live: bool = True) -> dict:
    """One dict: the declaration, the two facts, and every check between them."""
    profile = profile if profile is not None else profile_of(project)
    decl = declared(profile)
    m = mode(profile)
    src = source_scan(project)
    live_reason = ""
    if events is None and live:
        events, live_reason = live_events(project, profile)
    events = events or {}

    users = {k: int((v or {}).get("users") or 0) for k, v in events.items()}
    has_traffic = bool(users.get("first_open") or users.get("session_start")
                       or users.get("screen_view"))
    names = declared_names(decl)
    checks: list[dict] = []
    findings: list[str] = []

    # 1 — declared at all, in a shape the rest can read ---------------------- #
    if not m:
        checks.append(_check(
            "analytics.declared", "blocked",
            "no profile.analytics declaration — the planner cannot tell whether a "
            "missing number means 'nobody does this' or 'the app never said it'",
            "declare it: `devteam analytics <project> --draft` proposes a contract "
            "from the events the property already records; `--declare <file>` writes it"))
        return _result(profile, decl, m, src, events, checks, findings, live_reason)

    law = str(decl.get("law") or "").strip()
    if m == "none" and not law:
        checks.append(_check(
            "analytics.declared", "blocked",
            "mode 'none' must name the clause that forbids analytics",
            "add \"law\": \"Pn\" (the PRODUCT.md clause) so the exception is law, not inertia"))

    # 2 — the tree obeys the declared mode ---------------------------------- #
    conns = profile.get("connections") or {}
    # A `firebase` connection is two different facts: a property that would
    # receive events (with the SDK in the tree) or the App Distribution app the
    # release run uploads a build to (a delivery channel, never reader data).
    # 2026-11-22: a `firebase` entry with no SDK in the tree is the App
    # Distribution app the release run uploads to; treating it as telemetry
    # asked the owner to break QA.
    fb_property = bool(conns.get("firebase")) and bool(src.get("sdk"))
    ga_conn = bool(conns.get("ga4"))
    if m == "none":
        if src.get("sdk") or src.get("tracker"):
            checks.append(_check(
                "analytics.obeys", "blocked",
                f"mode 'none' ({law or 'no clause named'}) but {DELIVERY_REF} "
                f"carries the analytics SDK/tracker",
                "remove the SDK and the tracker — or amend the clause, which is the owner's act"))
        elif ga_conn or fb_property:
            named = ([f"connections.ga4 (property {conns['ga4'].get('property') or '?'})"]
                     if ga_conn else [])
            if fb_property:
                named.append("connections.firebase (the tree ships the SDK)")
            checks.append(_check(
                "analytics.obeys", "blocked",
                f"mode 'none' ({law or 'no clause named'}) but the profile declares "
                f"{' and '.join(named)} — a property to read for an app that must send nothing",
                "drop connections.ga4 (`devteam analytics <project> --forget-ga4`) — or "
                "amend the clause, which is the owner's act. A `firebase` entry with no "
                "SDK in the tree is App Distribution and may stay"))
        else:
            checks.append(_check("analytics.obeys", "ok",
                                 f"nothing is sent: no SDK, no tracker, no property ({law or 'no clause'})"))
        return _result(profile, decl, m, src, events, checks, findings, live_reason)

    # measured
    missing = [f"the {label}" for label, ok in (("SDK", src.get("sdk")),
                                                 ("tracker seam", src.get("tracker")))
               if not ok]
    if not has_ga4(profile):
        missing.append("connections.ga4")
    if missing:
        checks.append(_check(
            "analytics.obeys", "blocked",
            "mode 'measured' but missing " + ", ".join(missing),
            "wire firebase-analytics + an AnalyticsTracker seam (commonMain) delegating "
            "to FirebaseAnalytics (androidMain), and declare connections.ga4 {property}"))
    else:
        checks.append(_check("analytics.obeys", "ok",
                             "the SDK, the tracker seam and the property are all present"))

    # 2b — a measured app may not be run under a clause that forbids sending -- #
    #     Found 2026-11-22: a launcher declared "no telemetry ... nothing about
    #     the reader leaves the device" and shipped Firebase Analytics with the
    #     package names of the apps its reader opens, and the gate said nothing
    #     because it only ever compared the declaration to the tree and never to
    #     the law the project declares. The contradiction is the owner's to
    #     settle; a run may not settle it by quietly adding another event.
    conflict = silent_clause(project)
    if conflict:
        cid, text = conflict
        checks.append(_check(
            "analytics.obeys.law", "blocked",
            f"mode 'measured' but {cid} says \"{text[:120]}\" — the shipped tree "
            "sends product events under a clause that forbids sending them",
            f"one of the two is wrong, and only the owner can pick: amend {cid} to "
            "permit anonymous product events (never reader text or the apps they "
            f"open) — `devteam law {project.name} --amend {cid} --text \"…\" "
            "--requested-by \"<owner words>\"` — or drop the tracker"))

    # 3 — every required class is named, and named in the shipped code ------ #
    all_declared = [n for names_ in names.values() for n in names_]
    in_tree = tree_contains(project, all_declared) if all_declared else set()
    sent = emits(project, [n for n in all_declared if n in in_tree])
    for cid in REQUIRED:
        if cid == "money" and not src.get("billing"):
            continue
        got = names.get(cid) or []
        if not got:
            fix = _class_fix(project, cid, users, src)
            checks.append(_check(
                f"analytics.{cid}.declared", "blocked",
                f"class '{cid}' ({CLASS_TITLES[cid]}) is not declared — "
                f"{CLASS_WHY[cid]} is unanswerable", fix))
            continue
        absent = [n for n in got if n not in in_tree]
        if absent:
            checks.append(_check(
                f"analytics.{cid}.declared", "blocked",
                f"declared for '{cid}' but not in {DELIVERY_REF}: "
                + ", ".join(absent),
                "the declaration and the shipped tree disagree — rename the event or fix "
                "the declaration; an event only in the plan is an event nobody sends "
                "(a name GA4 still shows may be an old build, not the product)"))
        else:
            dead = [n for n in got if sent.get(n) is False]
            if dead:
                checks.append(_check(
                    f"analytics.{cid}.declared", "blocked",
                    f"declared for '{cid}' but nothing emits it in {DELIVERY_REF}: "
                    + ", ".join(dead)
                    + " — the name exists as a constant with no call site",
                    "send the event from the screen or repository that performs the act "
                    "(a declared constant is not an event: GA4 reads its absence as "
                    "'nobody does this')"))
            else:
                checks.append(_check(f"analytics.{cid}.declared", "ok", ", ".join(got)))

    # 4 — the money funnel, when the app sells something -------------------- #
    if src.get("billing"):
        halves = money_halves(decl)
        gone = [h for h in ("shown", "success") if not halves.get(h)]
        if gone:
            checks.append(_check(
                "analytics.money.declared", "blocked",
                "the tree has a billing path but the purchase funnel is undeclared "
                f"(missing: {', '.join(gone)})",
                "declare classes.money = {shown: [<paywall shown>], success: [<purchase "
                "completed>]} and emit those events from the app code — a paywall "
                "nobody records converts a product decision into a guess"))
        else:
            absent = [n for n in halves["shown"] + halves["success"] if n not in in_tree]
            if absent:
                checks.append(_check(
                    "analytics.money.declared", "blocked",
                    f"the purchase funnel is declared but these events are not in "
                    f"{DELIVERY_REF}: " + ", ".join(absent),
                    "the app sells and records no purchase — emit the events from the app "
                    "code: paywall_shown when the sheet opens and purchase_completed "
                    "on the confirmed purchase (the declaration names them)"))
            else:
                dead = [n for n in halves["shown"] + halves["success"]
                        if sent.get(n) is False]
                if dead:
                    checks.append(_check(
                        "analytics.money.declared", "blocked",
                        f"the purchase funnel is declared but nothing emits it in "
                        f"{DELIVERY_REF}: " + ", ".join(dead)
                        + " — the name exists as a constant with no call site",
                        "send the events from the billing path: paywall_shown when the "
                        "sheet opens, purchase_completed when Play confirms the purchase"))
                else:
                    checks.append(_check("analytics.money.declared", "ok",
                                         "shown: " + ", ".join(halves["shown"])
                                         + " · success: " + ", ".join(halves["success"])))

    # 5 — did a real user record it? --------------------------------------- #
    if events is None or (not has_traffic and not users):
        checks.append(_check(
            "analytics.lands", "unknown",
            f"no traffic in the {DAYS} days read"
            + (f" ({live_reason})" if live_reason else "")
            + " — nothing to verify against yet",
            "the declaration is checked against the tree; the live half proves itself "
            "once the property records a first open"))
    else:
        for cid in REQUIRED:
            if cid == "money" and not src.get("billing"):
                continue
            got = names.get(cid) or []
            if not got:
                continue
            # one gap, one row: if the class is not sent at all, the zero is a
            # consequence of that, not a second finding
            if any(c.get("id") == f"analytics.{cid}.declared" and c["status"] == "blocked"
                   for c in checks):
                continue
            landed = {n: users.get(n, 0) for n in got}
            if not any(landed.values()):
                checks.append(_check(
                    f"analytics.{cid}.lands", "blocked",
                    f"declared '{cid}' events recorded 0 users in {DAYS}d: "
                    + ", ".join(got),
                    "either the event never fires on the real path (fix the wiring) "
                    "or the surface has no users yet (that is a funnel finding, not a "
                    "quiet zero) — the planner cannot tell the two apart from a 0"))
            else:
                checks.append(_check(f"analytics.{cid}.lands", "ok",
                                     ", ".join(f"{n}={v}" for n, v in landed.items())))

        # money is not in REQUIRED (it only applies when the tree sells) but once
        # declared it is measured like everything else — and only when the
        # declaration itself held, or one gap would be reported twice
        if src.get("billing") and not any(c["id"] == "analytics.money.declared"
                                           and c["status"] == "blocked"
                                           for c in checks):
            halves = money_halves(decl)
            paid = [n for n in halves.get("shown", []) + halves.get("success", [])]
            if paid:
                landed = {n: users.get(n, 0) for n in paid}
                if not any(landed.values()):
                    checks.append(_check(
                        "analytics.money.lands", "blocked",
                        f"the purchase funnel is wired but recorded 0 users in {DAYS}d: "
                        + ", ".join(paid),
                        "open the paywall and buy the product on a device (a payment "
                        "sandbox licence counts) before believing the conversion rate"))
                else:
                    checks.append(_check(
                        "analytics.money.lands", "ok",
                        ", ".join(f"{n}={v}" for n, v in landed.items())))

        # the same action under two names across builds reads as a dead event and
        # a healthy one at once (a renamed completion event kept its old spelling
        # in GA4, where it outnumbers the shipped name)
        declared_all = set(all_declared)
        sibling: dict[str, str] = {}
        for n in sorted(declared_all):
            tokens = {t for t in n.split("_") if len(t) > 3}
            if not tokens:
                continue
            for other, ou in users.items():
                if other in declared_all or not ou or ou <= users.get(n, 0):
                    continue
                ot = {t for t in other.split("_") if len(t) > 3}
                if len(tokens & ot) >= 2:
                    sibling[n] = other
                    findings.append(
                        f"continuity: GA4 records '{other}' ({ou} users) beside the "
                        f"declared '{n}' ({users.get(n, 0)}) — a renamed event across "
                        "builds; the planner reads the old name as the live one")
                    break

        # the drop-off line: the declared pair, with the older spelling of the
        # completion folded in, and the numbers printed — never judged
        act, comp = names.get("activation") or [], names.get("completion") or []
        a_users = max((users.get(n, 0) for n in act), default=0)
        a_ev = act[0] if act else "activation"
        c_ev = comp[0] if comp else "completion"
        c_users = max(users.get(c_ev, 0), users.get(sibling.get(c_ev, ""), 0))
        if a_users >= DROPOFF_MIN_USERS:
            rate = c_users / a_users
            line = f"{a_ev} {a_users} → {c_ev} {c_users} ({rate:.0%})"
            if rate < DROPOFF_ALERT:
                findings.append(f"drop-off: {line} — under "
                                f"{DROPOFF_ALERT:.0%}; the gap is where the next "
                                "release earns its keep")
            else:
                findings.append(f"funnel: {line}")

        # A declared event with no users beside declared events that have them is
        # never a "0" the planner may read: either nobody reached that surface or
        # the call never fires. Those two answers are a task and a bug.
        silent: dict[str, list[str]] = {}
        for cid in REQUIRED:
            if cid == "money" and not src.get("billing"):
                continue
            got = names.get(cid) or []
            quiet = [n for n in got if not users.get(n)]
            loud = [n for n in got if users.get(n)]
            if quiet and loud:
                key = ", ".join(quiet)
                silent.setdefault(key, []).append(
                    ", ".join(f"{n} recorded {users[n]}" for n in loud))
        for quiet, witnesses in silent.items():
            findings.append(
                f"silent: {quiet} recorded 0 users in {DAYS}d while "
                + "; ".join(witnesses[:2])
                + " — the surface has no users yet, or its event never fires")

        # feature share: the ranking the planner re-prioritises from
        feats = {n: users.get(n, 0) for n in (names.get("features") or [])}
        ranked = sorted(((n, v) for n, v in feats.items() if v), key=lambda kv: -kv[1])
        if len(feats) < 2:
            checks.append(_check(
                "analytics.share", "warn",
                f"{len(feats)} feature event(s) declared — a share needs at least two "
                "surfaces to share between",
                "declare the optional surfaces (toggles, modes, tools) so their usage "
                "can be ranked against each other"))
        elif ranked:
            checks.append(_check(
                "analytics.share", "ok",
                "share: " + ", ".join(f"{n} {v}" for n, v in ranked)))
        else:
            checks.append(_check(
                "analytics.share", "blocked",
                "feature events declared but none recorded a user",
                "record the optional surfaces, or declare only the ones that exist"))

    return _result(profile, decl, m, src, events, checks, findings, live_reason)


def has_ga4(profile: dict) -> bool:
    return bool(((profile.get("connections") or {}).get("ga4") or {}).get("property"))


def silent_clause(project: Path) -> tuple[str, str] | None:
    """(clause id, text) of a declared clause that forbids sending, if any."""
    try:
        from . import law as law_mod
        ledger = law_mod.audit(project)
    except Exception:
        return None
    if not ledger.get("present"):
        return None
    for clause in ledger.get("clauses") or []:
        text = str(clause.get("text") or "")
        if _SILENT_LAW_RE.search(text):
            return str(clause.get("id") or "?"), text
    return None


def _class_fix(project: Path, cid: str, users: dict[str, int], src: dict) -> str:
    if cid == "money":
        return ("declare classes.money {shown, success} and record the paywall the "
                "tree already draws — the product sells, and nobody logs it")
    live = {k: v for k, v in users.items() if v}
    hint = ""
    if live:
        top = sorted(live.items(), key=lambda kv: -kv[1])[:3]
        hint = " The property already records: " + ", ".join(f"{n} ({v})" for n, v in top) + "."
    return (f"declare the events that name '{cid}' and make them fire on the real path."
            + hint)


def _result(profile, decl, m, src, events, checks, findings, live_reason) -> dict:
    return {
        "mode": m,
        "declaration": decl,
        "delivery_ref": src.get("ref"),
        "tree": {k: src.get(k) for k in ("available", "sdk", "tracker", "billing")},
        "events_30d": {k: (v or {}).get("users", 0) for k, v in (events or {}).items()},
        "live_reason": live_reason,
        "checks": checks,
        "findings": findings,
        "blocked": [c for c in checks if c["status"] == "blocked"],
        "warn": [c for c in checks if c["status"] == "warn"],
    }


# --------------------------------------------------------------------------- #
# consumers
# --------------------------------------------------------------------------- #
def doctor_checks(result: dict) -> list[tuple[str, bool, str]]:
    """doctor's (name, ok, fix) rows — a blocked analytics contract is a gate."""
    rows: list[tuple[str, bool, str]] = []
    for c in result.get("checks") or []:
        if c["status"] == "unknown":
            continue
        rows.append((f"analytics: {c['id']} — {c['detail']}",
                     c["status"] == "ok", c.get("fix") or ""))
    for line in result.get("findings") or []:
        rows.append((f"analytics: {line}", True, ""))
    return rows


def upgrades(result: dict, project_name: str) -> list[dict]:
    """One research upgrade per blocked check — the planner's own queue entry."""
    out: list[dict] = []
    for c in result.get("blocked") or []:
        cid = c["id"]
        kind = ("contract" if cid == "analytics.declared"
                else "instrumentation" if "declared" in cid or "obeys" in cid
                else "funnel")
        out.append({
            "id": f"analytics-{kind}",
            "title": f"Analytics contract — {cid.split('.', 1)[1].replace('.', ' ')}",
            "benefit": c["detail"],
            "how": c.get("fix") or "see `devteam analytics " + project_name + "`",
        })
    # de-duplicate by id, keeping the first (the widest) statement of the gap
    seen, uniq = set(), []
    for u in out:
        if u["id"] in seen:
            continue
        seen.add(u["id"])
        uniq.append(u)
    return uniq


def summary_line(result: dict) -> str:
    blocked = len(result.get("blocked") or [])
    if result.get("mode") == "none":
        return "analytics: off by clause" + (" — BLOCKED" if blocked else "")
    if not result.get("mode"):
        return "analytics: no contract declared"
    if blocked:
        return f"analytics: {blocked} blocked of {len(result.get('checks') or [])} check(s)"
    return f"analytics: contract holds ({len(result.get('checks') or [])} check(s))"


def write_declaration(project: Path, decl: dict) -> Path:
    """Write profile['analytics'] through the profile file (never hand-edited)."""
    from .core import load_json, save_json
    pf = project / ".pipeline" / "profile.json"
    profile = load_json(pf, {}) or {}
    profile["analytics"] = decl
    save_json(pf, profile)
    return pf


def forget_ga4(project: Path) -> tuple[Path | None, str]:
    """Drop connections.ga4 for an app that must send nothing.

    A property left wired for a silent app is a claim the planner can read as
    "no events yet" instead of "no events, by law". Only legal on mode 'none':
    on a measured app this would delete the contract's own evidence.
    """
    from .core import load_json, save_json
    pf = project / ".pipeline" / "profile.json"
    profile = load_json(pf, {}) or {}
    if mode(profile) != "none":
        return None, ("refused: the app is mode 'measured' — dropping its property "
                      "would delete the evidence the contract is checked against")
    conns = profile.get("connections") or {}
    if not conns.get("ga4"):
        return None, "nothing to drop: connections.ga4 is not declared"
    gone = conns.pop("ga4")
    save_json(pf, profile)
    kept = [k for k in ("firebase", "play_vitals", "play_listing") if conns.get(k)]
    return pf, (f"dropped connections.ga4 (property {gone.get('property') or '?'})"
                + (f" — kept {', '.join(kept)} (delivery, not telemetry)" if kept else ""))
