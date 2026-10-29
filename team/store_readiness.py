"""Play Store Readiness Agent — deterministic audit for Google Play compliance.

Checks manifest, SDK targets, permissions, store assets, localization, and
release configurations against current Google Play Store requirements.
Zero project names — generic across all Android / KMP projects.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import NamedTuple

from .core import profile_of, run


class ReadinessCheck(NamedTuple):
    category: str
    name: str
    passed: bool
    severity: str  # "BLOCKER", "WARNING", "INFO"
    detail: str
    fix: str = ""


# Google Play policy mandates targetSdk >= 34 (Android 14) for all app updates.
MIN_ACCEPTABLE_TARGET_SDK = 34
MIN_RECOMMENDED_MIN_SDK = 21
PLAY_APP_NAME_MAX_CHARS = 30
PLAY_SHORT_DESC_MAX_CHARS = 80
PLAY_FULL_DESC_MAX_CHARS = 4000
PLAY_WHATSNEW_MAX_CHARS = 500

RESTRICTED_PERMISSIONS = {
    "android.permission.MANAGE_EXTERNAL_STORAGE": "Scoped storage violation without Play approval",
    "android.permission.ACCESS_BACKGROUND_LOCATION": "Requires prominent in-app disclosure and Play approval",
    "android.permission.READ_SMS": "Requires Default SMS Handler status or Play exception",
    "android.permission.RECEIVE_SMS": "Requires Default SMS Handler status or Play exception",
    "android.permission.SEND_SMS": "Requires Default SMS Handler status or Play exception",
    "android.permission.READ_CALL_LOG": "Requires Default Phone/Dialer status or Play exception",
    "android.permission.WRITE_CALL_LOG": "Requires Default Phone/Dialer status or Play exception",
    "android.permission.SYSTEM_ALERT_WINDOW": "High-risk overlay permission; heavily restricted",
    "android.permission.REQUEST_INSTALL_PACKAGES": "Requires package installer justification",
    "android.permission.QUERY_ALL_PACKAGES": "Requires broad package querying justification",
}


def _find_gradle_files(project: Path) -> list[Path]:
    files: list[Path] = []
    for rel in ("app/build.gradle.kts", "app/build.gradle",
                "composeApp/build.gradle.kts", "composeApp/build.gradle",
                "build.gradle.kts", "build.gradle"):
        p = project / rel
        if p.is_file():
            files.append(p)
    return files


def _find_manifest_files(project: Path) -> list[Path]:
    manifests: list[Path] = []
    for cand in project.glob("**/AndroidManifest.xml"):
        # Skip build outputs
        if "build/" in str(cand) or ".gradle" in str(cand):
            continue
        manifests.append(cand)
    return manifests


def _read_gradle_property(content: str, key: str) -> str | None:
    # Match patterns like: targetSdk = 35 or targetSdk.set(35) or compileSdk = libs.versions.compileSdk.get().toInt()
    m = re.search(rf"{key}\s*(?:=\s*|\(\s*)([^\n\)]+)", content)
    if m:
        return m.group(1).strip().strip('"').strip("'")
    return None


def run_store_readiness_audit(project: Path) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    profile = profile_of(project)

    # ── 1. Target & Min SDK Compliance ────────────────────────────────────────
    gradle_files = _find_gradle_files(project)
    combined_gradle = "\n".join(f.read_text(errors="ignore") for f in gradle_files)

    # Check libs.versions.toml if referenced
    toml_path = project / "gradle/libs.versions.toml"
    combined_toml = toml_path.read_text(errors="ignore") if toml_path.is_file() else ""

    target_sdk = None
    min_sdk = None
    version_code = None
    version_name = None
    app_id = None

    # Search toml versions first
    for line in (combined_toml + "\n" + combined_gradle).splitlines():
        if "targetSdk" in line and not target_sdk:
            m = re.search(r"(\d{2,3})", line)
            if m:
                target_sdk = int(m.group(1))
        if "minSdk" in line and not min_sdk:
            m = re.search(r"(\d{2,3})", line)
            if m:
                min_sdk = int(m.group(1))
        if "versionCode" in line and not version_code:
            m = re.search(r"versionCode\s*(?:=\s*|\(\s*)(\d+)", line)
            if m:
                version_code = int(m.group(1))
        if "versionName" in line and not version_name:
            m = re.search(r'versionName\s*(?:=\s*|\(\s*)["\']([^"\']+)["\']', line)
            if m:
                version_name = m.group(1)
        if "applicationId" in line and not app_id:
            m = re.search(r'applicationId\s*(?:=\s*|\(\s*)["\']([^"\']+)["\']', line)
            if m:
                app_id = m.group(1)

    if target_sdk is not None:
        passed = target_sdk >= MIN_ACCEPTABLE_TARGET_SDK
        checks.append(ReadinessCheck(
            category="SDK Requirements",
            name="Target SDK (targetSdk)",
            passed=passed,
            severity="BLOCKER" if not passed else "INFO",
            detail=f"targetSdk is {target_sdk} (Google Play requires >= {MIN_ACCEPTABLE_TARGET_SDK})",
            fix=f"Update targetSdk in build.gradle.kts / libs.versions.toml to >= {MIN_ACCEPTABLE_TARGET_SDK}",
        ))
    else:
        checks.append(ReadinessCheck(
            category="SDK Requirements",
            name="Target SDK (targetSdk)",
            passed=False,
            severity="BLOCKER",
            detail="targetSdk could not be parsed from build scripts",
            fix="Declare targetSdk explicitly in module build.gradle.kts",
        ))

    if min_sdk is not None:
        passed = min_sdk >= MIN_RECOMMENDED_MIN_SDK
        checks.append(ReadinessCheck(
            category="SDK Requirements",
            name="Minimum SDK (minSdk)",
            passed=passed,
            severity="WARNING" if not passed else "INFO",
            detail=f"minSdk is {min_sdk} (Baseline: >= {MIN_RECOMMENDED_MIN_SDK})",
            fix=f"Raise minSdk to >= {MIN_RECOMMENDED_MIN_SDK}",
        ))

    # ── 2. App Identity & Versioning ──────────────────────────────────────────
    if app_id:
        valid_id = bool(re.match(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$", app_id))
        checks.append(ReadinessCheck(
            category="Identity & Versioning",
            name="Application ID",
            passed=valid_id,
            severity="BLOCKER" if not valid_id else "INFO",
            detail=f"applicationId: '{app_id}'",
            fix="Set a valid reverse-domain package name (e.g. com.example.app)",
        ))
    else:
        checks.append(ReadinessCheck(
            category="Identity & Versioning",
            name="Application ID",
            passed=False,
            severity="BLOCKER",
            detail="applicationId not found in build scripts",
            fix="Set applicationId in module build.gradle.kts",
        ))

    if version_code is not None and version_code > 0:
        checks.append(ReadinessCheck(
            category="Identity & Versioning",
            name="Version Code",
            passed=True,
            severity="INFO",
            detail=f"versionCode: {version_code}",
        ))
    else:
        checks.append(ReadinessCheck(
            category="Identity & Versioning",
            name="Version Code",
            passed=False,
            severity="BLOCKER",
            detail="versionCode is missing or non-positive",
            fix="Set versionCode to a positive integer in build.gradle.kts",
        ))

    if version_name:
        checks.append(ReadinessCheck(
            category="Identity & Versioning",
            name="Version Name",
            passed=True,
            severity="INFO",
            detail=f"versionName: '{version_name}'",
        ))
    else:
        checks.append(ReadinessCheck(
            category="Identity & Versioning",
            name="Version Name",
            passed=False,
            severity="WARNING",
            detail="versionName is missing",
            fix="Set versionName in build.gradle.kts",
        ))

    # ── 3. Manifest & Permissions Audit ───────────────────────────────────────
    manifests = _find_manifest_files(project)
    if not manifests:
        checks.append(ReadinessCheck(
            category="Manifest & Security",
            name="AndroidManifest.xml",
            passed=False,
            severity="BLOCKER",
            detail="No AndroidManifest.xml found in project",
            fix="Create an AndroidManifest.xml for the Android source set",
        ))
    else:
        restricted_found: list[str] = []
        missing_exported: list[str] = []
        has_icon = False

        for mf in manifests:
            try:
                tree = ET.parse(mf)
                root = tree.getroot()
            except Exception:
                continue

            for perm in root.findall(".//uses-permission"):
                pname = perm.attrib.get("{http://schemas.android.com/apk/res/android}name", "")
                if pname in RESTRICTED_PERMISSIONS:
                    restricted_found.append(f"{pname} ({RESTRICTED_PERMISSIONS[pname]})")

            app_node = root.find("application")
            if app_node is not None:
                icon_attr = app_node.attrib.get("{http://schemas.android.com/apk/res/android}icon")
                if icon_attr:
                    has_icon = True

                for comp in list(app_node.findall("activity")) + list(app_node.findall("service")) + list(app_node.findall("receiver")):
                    cname = comp.attrib.get("{http://schemas.android.com/apk/res/android}name", "unknown")
                    exported = comp.attrib.get("{http://schemas.android.com/apk/res/android}exported")
                    has_intent_filter = comp.find("intent-filter") is not None
                    if has_intent_filter and exported is None:
                        missing_exported.append(cname)

        checks.append(ReadinessCheck(
            category="Manifest & Security",
            name="Restricted Permissions",
            passed=len(restricted_found) == 0,
            severity="BLOCKER" if restricted_found else "INFO",
            detail="No restricted permissions detected" if not restricted_found else f"Declared restricted permissions: {', '.join(restricted_found)}",
            fix="Remove unneeded permissions or prepare required Google Play Declaration Forms",
        ))

        checks.append(ReadinessCheck(
            category="Manifest & Security",
            name="Explicit Component Export (Android 12+)",
            passed=len(missing_exported) == 0,
            severity="BLOCKER" if missing_exported else "INFO",
            detail="All intent-filtered components declare android:exported" if not missing_exported else f"Missing android:exported on: {', '.join(missing_exported)}",
            fix="Add android:exported='true|false' to all activities/services with intent filters",
        ))

        checks.append(ReadinessCheck(
            category="Manifest & Security",
            name="Application Icon Declaration",
            passed=has_icon,
            severity="BLOCKER" if not has_icon else "INFO",
            detail="android:icon declared on <application>" if has_icon else "android:icon missing on <application>",
            fix="Add android:icon='@mipmap/ic_launcher' to <application> in AndroidManifest.xml",
        ))

    # ── 4. Visual Launcher & Adaptive Icons ───────────────────────────────────
    has_adaptive_icon = any(project.glob("**/res/mipmap-anydpi-v26/ic_launcher.xml"))
    has_launcher_png = any(project.glob("**/res/mipmap-*/ic_launcher.png"))
    has_round_icon = any(project.glob("**/res/mipmap-*/ic_launcher_round.png")) or any(project.glob("**/res/mipmap-anydpi-v26/ic_launcher_round.xml"))

    checks.append(ReadinessCheck(
        category="Store Visual Assets",
        name="Adaptive Launcher Icon",
        passed=has_adaptive_icon or has_launcher_png,
        severity="BLOCKER" if not (has_adaptive_icon or has_launcher_png) else "INFO",
        detail="Adaptive / legacy launcher icons present in mipmap resources" if (has_adaptive_icon or has_launcher_png) else "No launcher icons found in mipmap resources",
        fix="Provide ic_launcher in mipmap directories (adaptive vector XML in mipmap-anydpi-v26 recommended)",
    ))

    checks.append(ReadinessCheck(
        category="Store Visual Assets",
        name="Round Launcher Icon",
        passed=has_round_icon,
        severity="WARNING" if not has_round_icon else "INFO",
        detail="ic_launcher_round present for round-icon launchers" if has_round_icon else "ic_launcher_round not found",
        fix="Provide ic_launcher_round in mipmap resources for circular launchers",
    ))

    # ── 5. Localization Completeness ──────────────────────────────────────────
    declared_locales = profile.get("locales", [])
    if declared_locales:
        missing_locales: list[str] = []
        for loc in declared_locales:
            # Check for values-<loc>/strings.xml
            loc_matches = list(project.glob(f"**/res/values-{loc}/strings.xml"))
            if not loc_matches:
                missing_locales.append(loc)
        passed_loc = len(missing_locales) == 0
        checks.append(ReadinessCheck(
            category="Localization",
            name=f"Declared Locales Completeness ({len(declared_locales)} locales)",
            passed=passed_loc,
            severity="BLOCKER" if not passed_loc else "INFO",
            detail=f"All {len(declared_locales)} declared locale string files present" if passed_loc else f"Missing string files for locales: {', '.join(missing_locales)}",
            fix="Ensure values-<loc>/strings.xml exists for every locale declared in profile.json",
        ))

    # ── 6. Release Build & Signing Configuration ──────────────────────────────
    has_signing = "signingConfigs" in combined_gradle and ("getByName(\"release\")" in combined_gradle or "create(\"release\")" in combined_gradle)
    has_keystore_ref = "keystore.properties" in combined_gradle or "storeFile" in combined_gradle
    checks.append(ReadinessCheck(
        category="Release Configuration",
        name="Release Signing Configuration",
        passed=has_signing and has_keystore_ref,
        severity="WARNING" if not (has_signing and has_keystore_ref) else "INFO",
        detail="Release signingConfig configured via keystore reference" if (has_signing and has_keystore_ref) else "Release signingConfig not fully configured in build.gradle.kts",
        fix="Configure signingConfigs.release with keystore properties for Play App Bundle signing",
    ))

    # ── 7. Privacy Policy & Data Safety ───────────────────────────────────────
    conns = profile.get("connections", {})
    privacy_url = (conns.get("play_store", {}).get("privacy_policy_url") or
                   profile.get("privacy_policy_url") or
                   "")
    # Also check if privacy doc exists in repo
    has_privacy_doc = (project / "PRIVACY.md").is_file() or (project / "docs/privacy.md").is_file()
    has_privacy = bool(privacy_url) or has_privacy_doc
    checks.append(ReadinessCheck(
        category="Privacy & Policy",
        name="Privacy Policy URL / Document",
        passed=has_privacy,
        severity="WARNING" if not has_privacy else "INFO",
        detail=f"Privacy policy present ({privacy_url or 'in-repo privacy doc'})" if has_privacy else "No privacy policy URL declared in profile.json or PRIVACY.md",
        fix="Declare privacy_policy_url in profile.json or create PRIVACY.md (mandatory for Google Play Store)",
    ))

    return checks


def format_readiness_report(project: Path, checks: list[ReadinessCheck]) -> tuple[str, bool]:
    lines: list[str] = []
    lines.append(f"PLAY STORE READINESS AUDIT: {project.name}")
    lines.append("=" * 60)

    categories: dict[str, list[ReadinessCheck]] = {}
    for c in checks:
        categories.setdefault(c.category, []).append(c)

    has_blockers = False
    has_warnings = False

    for cat, cat_checks in categories.items():
        lines.append(f"\n[{cat.upper()}]")
        for c in cat_checks:
            if c.passed:
                mark = "✓ PASS"
            elif c.severity == "BLOCKER":
                mark = "✗ BLOCKER"
                has_blockers = True
            elif c.severity == "WARNING":
                mark = "▲ WARN"
                has_warnings = True
            else:
                mark = "· INFO"
            lines.append(f"  {mark:<10} {c.name}: {c.detail}")
            if not c.passed and c.fix:
                lines.append(f"             ↳ Fix: {c.fix}")

    lines.append("\n" + "=" * 60)
    if has_blockers:
        overall = "BLOCKED — Fix blockers before submitting to Google Play"
        is_ready = False
    elif has_warnings:
        overall = "NEEDS REVIEW — Ready for internal testing; review warnings before production"
        is_ready = True
    else:
        overall = "READY FOR GOOGLE PLAY STORE SUBMISSION"
        is_ready = True

    lines.append(f"OVERALL STATUS: {overall}")
    return "\n".join(lines), is_ready
