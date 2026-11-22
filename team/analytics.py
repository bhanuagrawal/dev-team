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

  * the delivery tree  — does the code name the event at all?
  * the GA4 property   — did a real user record it in the last 30 days?

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
    ga_conn = bool(conns.get("ga4") or conns.get("firebase"))
    if m == "none":
        if src.get("sdk") or src.get("tracker"):
            checks.append(_check(
                "analytics.obeys", "blocked",
                f"mode 'none' ({law or 'no clause named'}) but {DELIVERY_REF} "
                f"carries the analytics SDK/tracker",
                "remove the SDK and the tracker — or amend the clause, which is the owner's act"))
        elif ga_conn:
            checks.append(_check(
                "analytics.obeys", "blocked",
                f"mode 'none' ({law or 'no clause named'}) but the profile declares "
                f"connections {', '.join(k for k in ('ga4', 'firebase') if conns.get(k))} "
                "— a connected property for an app that must send nothing",
                "drop the connections (`profile.connections`) — or amend the clause"))
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

    # 3 — every required class is named, and named in the shipped code ------ #
    all_declared = [n for names_ in names.values() for n in names_]
    in_tree = tree_contains(project, all_declared) if all_declared else set()
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
