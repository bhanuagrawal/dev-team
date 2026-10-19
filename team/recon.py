"""Competitor recon — the deterministic core of the MANUAL intel plane.

This is a SECOND workflow, deliberately separate from the nightly run (see
README → *The intel plane*). It is invoked by hand:

    devteam recon <project> static <apk|package> [--apk FILE] [--device SERIAL]
    devteam recon <project> findings <package> --file findings.json
    devteam recon <project> prompt <package> [--question ...]
    devteam recon <project> list | show <package>

Why separate: a competitor inspection is long-running (minutes), needs an
emulator, and asks questions only a human can answer. None of that belongs in
the 90-minute nightly. So this module holds the DETERMINISTIC half — acquire the
APK, read what is statically knowable — and writes ONE versioned report per app
to `.pipeline/competitors/<package>.json`.

That directory is the ONLY boundary with the delivery plane: the planner's
collectors read it as a research signal (`research._competitors_pull`), never
wait on it, and never write it. Nothing here calls an LLM; the agentic half
(driving the emulator) is done by a subagent following `skills/recon/SKILL.md`
and merged back through `findings`, so every write still goes through code.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

from .core import load_json, now_iso, pipeline_dir, run, save_json

COMPETITORS_DIR = "competitors"
SCHEMA_VERSION = 1

# Generic SDK signatures, searched as raw byte substrings inside the APK's dex
# files (class names are stored slash-separated). Deliberately a small, generic
# catalogue — no project names — and growable: add a row, never special-case a
# project. `capability` is the plain-words bucket the report groups it under.
SDK_MARKERS: dict[str, tuple[str, str, list[str]]] = {
    # id: (label, capability, byte substrings)
    "firebase-analytics": ("Firebase Analytics", "analytics",
                           ["com/google/firebase/analytics"]),
    "gms-analytics": ("Google Analytics (GMS)", "analytics",
                      ["com/google/android/gms/analytics"]),
    "amplitude": ("Amplitude", "analytics", ["com/amplitude"]),
    "mixpanel": ("Mixpanel", "analytics", ["com/mixpanel"]),
    "segment": ("Segment", "analytics", ["com/segment/analytics"]),
    "posthog": ("PostHog", "analytics", ["com/posthog"]),
    "firebase-crashlytics": ("Firebase Crashlytics", "crash",
                             ["com/google/firebase/crashlytics"]),
    "sentry": ("Sentry", "crash", ["io/sentry"]),
    "bugsnag": ("Bugsnag", "crash", ["com/bugsnag"]),
    "play-billing": ("Google Play Billing", "monetization",
                     ["com/android/billingclient"]),
    "revenuecat": ("RevenueCat", "monetization", ["com/revenuecat"]),
    "stripe": ("Stripe", "payments", ["com/stripe/android"]),
    "admob": ("AdMob", "ads", ["com/google/android/gms/ads"]),
    "facebook-ads": ("Facebook Audience Network", "ads",
                     ["com/facebook/ads"]),
    "applovin": ("AppLovin", "ads", ["com/applovin"]),
    "unity-ads": ("Unity Ads", "ads", ["com/unity3d/ads"]),
    "appsflyer": ("AppsFlyer", "attribution", ["com/appsflyer"]),
    "adjust": ("Adjust", "attribution", ["com/adjust/sdk"]),
    "firebase-auth": ("Firebase Auth", "auth", ["com/google/firebase/auth"]),
    "facebook-login": ("Facebook Login", "auth", ["com/facebook/login"]),
    "google-signin": ("Google Sign-In", "auth",
                      ["com/google/android/gms/auth/api/signin"]),
    "firebase-messaging": ("Firebase Cloud Messaging", "push",
                           ["com/google/firebase/messaging"]),
    "onesignal": ("OneSignal", "push", ["com/onesignal"]),
    "firebase-remoteconfig": ("Firebase Remote Config", "remote-config",
                              ["com/google/firebase/remoteconfig"]),
    "firebase-firestore": ("Cloud Firestore", "sync",
                           ["com/google/firebase/firestore"]),
    "firebase-rtdb": ("Firebase Realtime Database", "sync",
                      ["com/google/firebase/database"]),
    "mlkit": ("ML Kit", "ml", ["com/google/mlkit"]),
    "gms-maps": ("Google Maps", "maps", ["com/google/android/gms/maps"]),
    "health-connect": ("Android Health Connect", "health",
                       ["androidx/health/connect"]),
    "workmanager": ("WorkManager", "background",
                    ["androidx/work/impl"]),
    "room": ("Room", "local-db", ["androidx/room"]),
    "billing-android": ("Android Billing", "monetization",
                        ["com/android/billingclient/api"]),
    "intercom": ("Intercom", "support", ["io/intercom"]),
    "zendesk": ("Zendesk", "support", ["com/zendesk"]),
    "braze": ("Braze", "engagement", ["com/braze"]),
    "iterable": ("Iterable", "engagement", ["com/iterable"]),
}


def competitors_dir(project: Path) -> Path:
    return pipeline_dir(project) / COMPETITORS_DIR


def report_path(project: Path, package: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", (package or "unknown").strip())
    return competitors_dir(project) / f"{safe}.json"


# --------------------------------------------------------------------------- #
# Android toolchain: find what is actually installed, degrade gracefully
# --------------------------------------------------------------------------- #

def _android_home() -> Path | None:
    import os
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        v = os.environ.get(env)
        if v and Path(v).is_dir():
            return Path(v)
    cand = Path.home() / "Library" / "Android" / "sdk"
    return cand if cand.is_dir() else None


def _build_tool(name: str) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    home = _android_home()
    if not home:
        return None
    if name == "apkanalyzer":
        for pat in ("cmdline-tools/latest/bin", "cmdline-tools/*/bin"):
            for d in sorted(home.glob(pat), reverse=True):
                exe = d / "apkanalyzer"
                if exe.exists():
                    return str(exe)
        return None
    bt = home / "build-tools"
    if bt.is_dir():
        for d in sorted(bt.iterdir(), reverse=True):
            exe = d / name
            if exe.exists():
                return str(exe)
    return None


def _parse_badging(text: str) -> dict:
    """Parse `aapt2 dump badging` into the generic facts we care about."""
    facts: dict = {"tool": "aapt2", "permissions": [], "features": [],
                   "activities": []}
    m = re.search(r"package:\s*name='([^']*)'", text)
    if m:
        facts["package"] = m.group(1)
    m = re.search(r"versionCode='([^']*)'", text)
    if m:
        facts["version_code"] = m.group(1)
    m = re.search(r"versionName='([^']*)'", text)
    if m:
        facts["version_name"] = m.group(1)
    # aapt2 (build-tools 34+) says `minSdkVersion:'36'`; older aapt said
    # `sdkVersion:'24'`. Accept both — and note neither pattern matches
    # `targetSdkVersion` (capital S), which is parsed separately below.
    for pat in (r"minSdkVersion:'([^']*)'", r"sdkVersion:'([^']*)'"):
        m = re.search(pat, text)
        if m:
            facts["min_sdk"] = m.group(1)
            break
    m = re.search(r"targetSdkVersion:'([^']*)'", text)
    if m:
        facts["target_sdk"] = m.group(1)
    m = re.search(r"application-label(?:-[A-Za-z-]+)?:'([^']*)'", text)
    if m:
        facts["app_name"] = m.group(1)
    facts["permissions"] = re.findall(r"uses-permission:\s*name='([^']*)'", text)
    facts["features"] = [f for f in
                         re.findall(r"uses-feature:\s*name='([^']*)'", text)
                         if not f.startswith("android.hardware.")]
    facts["hardware_features"] = re.findall(
        r"uses-feature:\s*name='(android\.hardware\.[^']*)'", text)
    facts["activities"] = re.findall(
        r"launchable-activity:\s*name='([^']*)'", text)
    return facts


def _facts(apk: Path) -> dict:
    """Manifest facts via the first available tool; {} when none is installed
    (the dex SDK scan below still works without any Android tool at all)."""
    aapt2 = _build_tool("aapt2")
    if aapt2:
        r = run([aapt2, "dump", "badging", str(apk)], None, timeout=90)
        if r.returncode == 0 and (r.stdout or "").strip():
            return _parse_badging(r.stdout)
    apkanalyzer = _build_tool("apkanalyzer")
    if apkanalyzer:
        def one(*args: str) -> str:
            rr = run([apkanalyzer, *args, str(apk)], None, timeout=90)
            return (rr.stdout or "").strip() if rr.returncode == 0 else ""
        facts = {"tool": "apkanalyzer", "permissions": [], "features": [],
                 "activities": []}
        facts["package"] = one("manifest", "application-id")
        facts["version_code"] = one("manifest", "version-code")
        facts["version_name"] = one("manifest", "version-name")
        facts["min_sdk"] = one("manifest", "min-sdk")
        facts["target_sdk"] = one("manifest", "target-sdk")
        facts["permissions"] = [l.strip() for l
                                in one("manifest", "permissions").splitlines()
                                if l.strip()]
        return {k: v for k, v in facts.items() if v not in ("", None)}
    return {}


# --------------------------------------------------------------------------- #
# static pass
# --------------------------------------------------------------------------- #

def _sdk_scan(apk: Path) -> list[dict]:
    """Byte-scan the dex files for known SDK signatures. No toolchain needed."""
    try:
        with zipfile.ZipFile(apk) as z:
            blobs = [z.read(n) for n in z.namelist()
                     if n.endswith(".dex") or n.endswith(".jar")]
    except (OSError, zipfile.BadZipFile):
        return []
    out: list[dict] = []
    for sid, (label, capability, markers) in SDK_MARKERS.items():
        raw = [m.encode() for m in markers]
        if any(any(m in blob for m in raw) for blob in blobs):
            out.append({"id": sid, "label": label, "capability": capability})
    return sorted(out, key=lambda d: (d["capability"], d["id"]))


def _capabilities(sdks: list[dict]) -> list[str]:
    return sorted({s["capability"] for s in sdks})


def _fingerprint(apk: Path) -> str:
    """Cheap identity when there is no versionCode (no Android tool installed):
    size + a short content hash. Lets `static` stay idempotent on side-loaded
    APKs too."""
    import hashlib
    try:
        data = apk.read_bytes()
    except OSError:
        return ""
    return f"{len(data)}-{hashlib.sha256(data).hexdigest()[:12]}"


def analyze_apk(apk: Path, source: str = "apk") -> dict:
    """The deterministic static report body (no IO to .pipeline)."""
    facts = _facts(apk)
    sdks = _sdk_scan(apk)
    package = facts.get("package") or apk.stem
    return {
        "schema_version": SCHEMA_VERSION,
        "package": package,
        "app_name": facts.get("app_name"),
        "version_code": facts.get("version_code"),
        "version_name": facts.get("version_name"),
        "apk_fingerprint": _fingerprint(apk),
        "source": source,
        "apk_path": str(apk),
        "inspected_at": now_iso(),
        "static": {
            "tool": facts.get("tool") or "dex-scan-only",
            "min_sdk": facts.get("min_sdk"),
            "target_sdk": facts.get("target_sdk"),
            "permissions": facts.get("permissions", []),
            "features": facts.get("features", []),
            "hardware_features": facts.get("hardware_features", []),
            "launcher_activity": (facts.get("activities") or [None])[0],
            "sdks": sdks,
            "capabilities": _capabilities(sdks),
        },
        "dynamic": {"done": False, "screens": [], "notes": ""},
        "findings": [],
        "questions": [],
        "confidence": "medium" if facts.get("tool") else "low",
    }


def pull_from_device(package: str, device: str | None = None) -> Path:
    """adb pull the installed APK(s) for a package (base + splits are merged by
    installing from the same source; we pull the base and note the rest)."""
    adb = shutil.which("adb") or str(
        (Path.home() / "Library/Android/sdk/platform-tools/adb"))
    pre = [adb] + (["-s", device] if device else [])
    r = run([*pre, "shell", "pm", "path", package], None, timeout=60)
    paths = re.findall(r"package:(/\S+\.apk)", r.stdout or "")
    if not paths:
        raise SystemExit(f"package not installed on device: {package} "
                         f"(adb said: {(r.stdout or r.stderr or '').strip()[:200]})")
    import tempfile
    dest = Path(tempfile.mkdtemp(prefix="devteam-recon-"))
    pulled = []
    for i, remote in enumerate(paths):
        name = f"{package}-{i}.apk"
        rr = run([*pre, "pull", remote, str(dest / name)], None, timeout=180)
        if rr.returncode == 0:
            pulled.append(dest / name)
    if not pulled:
        raise SystemExit(f"adb pull failed for {package}")
    return pulled[0]


def static_pass(project: Path, apk: Path, source: str = "apk",
                force: bool = False, log=None) -> dict:
    """Analyze an APK and write/refresh its report. Cached by version_code."""
    say = log or (lambda *_a, **_k: None)
    facts = _facts(apk)
    package = facts.get("package") or apk.stem
    rp = report_path(project, package)
    existing = load_json(rp)
    vc = facts.get("version_code")
    fp = _fingerprint(apk)
    unchanged = existing and not force and (
        (vc and str(existing.get("version_code")) == str(vc))
        or (not vc and existing.get("apk_fingerprint") == fp))
    if unchanged:
        say(f"recon: {package} "
            f"{(('v' + str(vc)) if vc else fp)} already inspected — up to date "
            f"(use --force to refresh)")
        return existing
    report = analyze_apk(apk, source=source)
    # keep agent findings/questions from a previous version of the same app
    if existing:
        report["dynamic"] = existing.get("dynamic", report["dynamic"])
        report["findings"] = existing.get("findings", [])
        report["questions"] = existing.get("questions", [])
    save_json(rp, report)
    caps = ", ".join(report["static"]["capabilities"]) or "none detected"
    say(f"recon: {report['package']} v{report.get('version_name') or '?'} "
        f"({report['static']['tool']}) → {rp.name}")
    say(f"  capabilities: {caps}")
    say(f"  sdks: {len(report['static']['sdks'])}, "
        f"permissions: {len(report['static']['permissions'])}")
    return report


def merge_findings(project: Path, package: str, payload: dict, log=None) -> dict:
    """Merge a subagent's observations into the report (writes stay in code).

    `payload` shape: {"findings": [{"question","answer","evidence","confidence"}],
                      "dynamic": {"done": true, "screens": [...], "notes": "..."},
                      "questions": [...], "confidence": "high|medium|low"}"""
    say = log or (lambda *_a, **_k: None)
    rp = report_path(project, package)
    report = load_json(rp)
    if not report:
        raise SystemExit(f"no report for {package} — run `recon static` first")
    for key in ("findings", "questions"):
        if payload.get(key):
            report[key] = payload[key]
    if payload.get("dynamic"):
        report["dynamic"] = {**report.get("dynamic", {}), **payload["dynamic"]}
    if payload.get("confidence"):
        report["confidence"] = payload["confidence"]
    report["inspected_at"] = now_iso()
    save_json(rp, report)
    say(f"recon: findings merged into {rp.name} "
        f"({len(report.get('findings', []))} finding(s))")
    return report


# --------------------------------------------------------------------------- #
# read side: the ONE boundary the delivery plane consumes
# --------------------------------------------------------------------------- #

def _report_row(report: dict) -> dict:
    static = report.get("static") or {}
    return {
        "package": report.get("package"),
        "app_name": report.get("app_name"),
        "version_name": report.get("version_name"),
        "version_code": report.get("version_code"),
        "capabilities": static.get("capabilities", []),
        "sdks": [s.get("label") for s in static.get("sdks", [])],
        "permissions_count": len(static.get("permissions", [])),
        "dynamic_done": bool((report.get("dynamic") or {}).get("done")),
        "findings_count": len(report.get("findings", [])),
        "confidence": report.get("confidence"),
        "inspected_at": report.get("inspected_at"),
    }


def load_reports(project: Path) -> list[dict]:
    d = competitors_dir(project)
    if not d.is_dir():
        return []
    out = [r for r in (load_json(p) for p in sorted(d.glob("*.json")))
           if isinstance(r, dict) and r.get("package")]
    return out


def as_signal(project: Path) -> dict:
    """The research-signal view: a compact summary the planner can cite without
    reading every report. Absent dir → status 'none' (never an error)."""
    reports = load_reports(project)
    if not reports:
        return {"status": "none", "count": 0, "reports": [],
                "capability_coverage": {}}
    coverage: dict[str, list[str]] = {}
    for r in reports:
        for cap in ((r.get("static") or {}).get("capabilities") or []):
            coverage.setdefault(cap, []).append(r.get("package"))
        for f in r.get("findings", []):
            cap = f.get("capability")
            if cap:
                coverage.setdefault(cap, []).append(r.get("package"))
    return {
        "status": "ok",
        "count": len(reports),
        "reports": [_report_row(r) for r in reports],
        "capability_coverage": {k: sorted(set(v)) for k, v in sorted(coverage.items())},
    }


# --------------------------------------------------------------------------- #
# the agentic half: hand the subagent an exact prompt (mirrors developer-prompt)
# --------------------------------------------------------------------------- #

DYN_TMPL = """You are the RECON AGENT — the manual intel plane of the dev-team.

TARGET APP
  package:      {package}
  app name:     {app_name}
  version:      {version_name} (versionCode {version_code})
  known (static pass): capabilities={capabilities}
  report file:  {report_path}

WHAT WE WANT TO KNOW (answer each, with evidence):
{questions}

HOW TO WORK (an emulator + the host's own tools; a human is present — ask them
when a question needs a real account or a judgement call):
1. Drive the emulator through the `android` CLI and its official Google Android
   skills. Read the UI tree (`android layout` / `uiautomator dump`) FIRST — it is
   structured and reliable. Use pixel-clicks (computer-use / desktop tools) ONLY
   for surfaces with an empty tree (canvas, WebView).
2. The app must be installed on a Play-enabled emulator. Prefer the deterministic
   path: `devteam recon {project} install {package}` — it drives the Play Store,
   taps Install, waits, pulls the APK and runs the static pass. If the emulator
   is not signed into Play it stops with an OWNER ASK: a human signs in once on
   the emulator window (never a password in a prompt). Start an emulator with
   `with-emulator <avd> -- <cmd...>` (teardown via an EXIT trap), or keep one up
   for the session and stop it yourself when done. NEVER stop an emulator you did
   not start.
3. Work ONLY on the target app. Never touch any project's source code.
4. If a question needs a login/secret/payment you cannot supply, do NOT guess:
   add it to `questions` and ask the human in the room.

WHAT TO RETURN (write this file, then stop):
  {findings_file}
  {{"findings": [{{"question": "...", "answer": "...",
                  "evidence": "screen/file/observation", "confidence": "high|medium|low",
                  "capability": "optional bucket"}}],
   "dynamic": {{"done": true, "screens": ["Home", "Paywall"], "notes": "..."}},
   "questions": ["anything the human must answer"],
   "confidence": "high|medium|low"}}

Then run, so the write goes through code:
  devteam recon {project} findings {package} --file {findings_file}
"""


def render_dynamic_prompt(project: Path, report: dict,
                          questions: list[str] | None = None) -> str:
    package = report.get("package") or "?"
    static = report.get("static") or {}
    qs = questions or report.get("questions") or [
        "What does the first-run / onboarding flow ask for?",
        "How is the core value delivered (the main loop)?",
        "Where are the paywalls / upgrade prompts, and what do they gate?",
        "Which of the detected capabilities are actually user-visible?",
        "What is present that our app lacks (and vice versa)?",
    ]
    ffile = f".pipeline/competitors/{package}.findings.json"
    return DYN_TMPL.format(
        package=package,
        app_name=report.get("app_name") or "?",
        version_name=report.get("version_name") or "?",
        version_code=report.get("version_code") or "?",
        capabilities=", ".join(static.get("capabilities") or []) or "none detected",
        report_path=str(report_path(project, package)),
        questions="\n".join(f"  - {q}" for q in qs),
        findings_file=ffile,
        project=project.name,
    )
