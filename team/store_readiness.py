"""Play Store Readiness Agent — deterministic audit for Google Play compliance.

Checks manifest, SDK targets, permissions, store assets, localization, and
release configurations against current Google Play Store requirements.
Zero project names — generic across all Android / KMP projects.
"""
from __future__ import annotations

import json
import re
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from typing import NamedTuple

from .core import RunLog, profile_of, run, save_json, today


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


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as f:
            data = f.read(24)
            if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
                w, h = struct.unpack(">II", data[16:24])
                return w, h
    except Exception:
        pass
    return None


def _find_listing_dir(project: Path) -> Path | None:
    for rel in ("distribution/listing/en-US", "distribution/listing",
                "fastlane/metadata/android/en-US", "src/main/play/listings/en-US"):
        p = project / rel
        if p.is_dir():
            return p
    return None


def _make_solid_png(w: int, h: int, r: int, g: int, b: int,
                    border_rgb: tuple[int, int, int] | None = None) -> bytes:
    raw = bytearray()
    row = bytearray()
    for x in range(w):
        if border_rgb and (x < 8 or x >= w - 8):
            row.extend([border_rgb[0], border_rgb[1], border_rgb[2], 255])
        else:
            row.extend([r, g, b, 255])
    for y in range(h):
        raw.append(0)  # filter type None
        if border_rgb and (y < 8 or y >= h - 8):
            border_row = bytearray()
            for _ in range(w):
                border_row.extend([border_rgb[0], border_rgb[1], border_rgb[2], 255])
            raw.extend(border_row)
        else:
            raw.extend(row)
    comp = zlib.compress(bytes(raw), 9)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" +
            struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)) +
            struct.pack(">I", len(comp)) + b"IDAT" + comp + struct.pack(">I", zlib.crc32(b"IDAT" + comp)) +
            struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND")))


def _strip_code_comments(text: str) -> str:
    """Drop // and /* */ comments so prose is never parsed as a declaration."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _convention_constants(src: Path, combined_gradle: str) -> str:
    """Declarations that live in build-logic convention plugins.

    A project may set `targetSdk = MyAndroid.TARGET_SDK` and define that constant in
    a `build-logic` Kotlin source file; the value then exists nowhere in the module
    build script. Read those sources so the audit sees the real number instead of
    reporting an unparseable BLOCKER.
    """
    chunks = [combined_gradle]
    try:
        for path in src.rglob("*.kt"):
            text = str(path)
            if "/build/" in text or "/.gradle/" in text:
                continue
            if "build-logic" not in text and "convention" not in text.lower():
                continue
            if "SDK" not in path.read_text(errors="ignore"):
                continue
            chunks.append(path.read_text(errors="ignore"))
    except OSError:
        pass
    return _strip_code_comments("\n".join(chunks))


def _sdk_number(gradle_src: str, constants_src: str, field: str, constant: str):
    """Explicit `field = 34` assignment first, then a named constant in build-logic."""
    patterns = (
        rf"\b{field}(?:Version)?\s*(?:=\s*|\(\s*)(\d{{2,3}})\b",
        rf"\b{constant}\b\s*(?::\s*Int\s*)?=\s*(\d{{2,3}})\b",
        rf"\b{field}(?:Version)?\s*=\s*[A-Za-z_][\w.]*\b",
    )
    m = re.search(patterns[0], gradle_src) or re.search(patterns[1], constants_src)
    if m:
        return int(m.group(1))
    # A named constant reference: resolve it from the convention sources.
    if re.search(patterns[2], gradle_src):
        m = re.search(rf"\b{constant}\b\s*(?::\s*Int\s*)?=\s*(\d{{2,3}})\b", constants_src)
        if m:
            return int(m.group(1))
    return None


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


def run_store_readiness_audit(project: Path, tree: Path | None = None) -> list[ReadinessCheck]:
    """Audit the app's Play submission readiness.

    `project` is the owner's checkout: it holds the profile (targets, connections)
    and is where the generated pack is written. `tree` is the tree the FILES are
    read from — the shipped one (origin/main worktree) whenever a caller knows it:
    the owner's checkout can lag origin or carry unrelated WIP, and auditing it
    reports a blocker that the shipped app does not have (or misses one it does).
    """
    checks: list[ReadinessCheck] = []
    profile = profile_of(project)
    src = tree if (tree is not None and Path(tree).is_dir()) else project

    # ── 1. Target & Min SDK Compliance ────────────────────────────────────────
    gradle_files = _find_gradle_files(src)
    combined_gradle = "\n".join(f.read_text(errors="ignore") for f in gradle_files)

    # Check libs.versions.toml if referenced
    toml_path = src / "gradle/libs.versions.toml"
    combined_toml = toml_path.read_text(errors="ignore") if toml_path.is_file() else ""

    target_sdk = None
    min_sdk = None
    version_code = None
    version_name = None
    app_id = None

    # Comments are prose, not declarations: a comment like "compileSdk/targetSdk ...
    # and Java 17 come from the convention plugin" used to be read as targetSdk=17,
    # i.e. a red BLOCKER on a Play-compliant app. Strip them before parsing, and
    # read only real assignments.
    gradle_src = _strip_code_comments(combined_toml + "\n" + combined_gradle)
    constants_src = _convention_constants(src, combined_gradle)

    for line in gradle_src.splitlines():
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

    target_sdk = _sdk_number(gradle_src, constants_src, "targetSdk", "TARGET_SDK")
    min_sdk = _sdk_number(gradle_src, constants_src, "minSdk", "MIN_SDK")

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
    manifests = _find_manifest_files(src)
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
    has_adaptive_icon = any(src.glob("**/res/mipmap-anydpi-v26/ic_launcher.xml"))
    has_launcher_png = any(src.glob("**/res/mipmap-*/ic_launcher.png"))
    has_round_icon = any(src.glob("**/res/mipmap-*/ic_launcher_round.png")) or any(src.glob("**/res/mipmap-anydpi-v26/ic_launcher_round.xml"))

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

    # Check if launcher icon still uses the default Android Studio starter purple
    is_starter_template = False
    for bf in src.glob("**/res/drawable*/ic_launcher_background.xml"):
        if "build/" in str(bf) or ".gradle" in str(bf) or ".pipeline" in str(bf):
            continue
        text = bf.read_text(errors="ignore")
        if "#4F378A" in text.upper():
            is_starter_template = True
            break
    checks.append(ReadinessCheck(
        category="Store Visual Assets",
        name="Launcher Icon Brand Customization",
        passed=not is_starter_template,
        severity="WARNING" if is_starter_template else "INFO",
        detail="Custom branded launcher icon vector detected" if not is_starter_template else "Launcher background still uses default Android Studio purple (#4F378A)",
        fix="Replace starter ic_launcher vector assets with custom branded colors and iconography",
    ))

    # ── 5. Storefront Listing Copy ────────────────────────────────────────────
    listing_dir = _find_listing_dir(src)
    if not listing_dir:
        checks.append(ReadinessCheck(
            category="Storefront Listing Copy",
            name="Store Listing Directory",
            passed=False,
            severity="WARNING",
            detail="No distribution/listing/en-US or fastlane metadata directory found",
            fix="Run `devteam store-readiness <project> --generate` to synthesize storefront listing files",
        ))
    else:
        title_file = listing_dir / "title.txt"
        if title_file.is_file():
            t_len = len(title_file.read_text(errors="ignore").strip())
            passed = 0 < t_len <= PLAY_APP_NAME_MAX_CHARS
            checks.append(ReadinessCheck(
                category="Storefront Listing Copy",
                name="App Title",
                passed=passed,
                severity="WARNING" if not passed else "INFO",
                detail=f"Title length: {t_len}/{PLAY_APP_NAME_MAX_CHARS} chars" if passed else f"Title exceeds maximum length ({t_len}/{PLAY_APP_NAME_MAX_CHARS} chars)",
                fix=f"Ensure title is <= {PLAY_APP_NAME_MAX_CHARS} chars",
            ))
        else:
            checks.append(ReadinessCheck(
                category="Storefront Listing Copy",
                name="App Title",
                passed=False,
                severity="WARNING",
                detail="title.txt missing from listing directory",
                fix="Create title.txt with app name (<= 30 chars)",
            ))

        short_file = listing_dir / "short_description.txt" if (listing_dir / "short_description.txt").is_file() else listing_dir / "short-description.txt"
        if short_file.is_file():
            s_len = len(short_file.read_text(errors="ignore").strip())
            passed = 0 < s_len <= PLAY_SHORT_DESC_MAX_CHARS
            checks.append(ReadinessCheck(
                category="Storefront Listing Copy",
                name="Short Description",
                passed=passed,
                severity="WARNING" if not passed else "INFO",
                detail=f"Short description length: {s_len}/{PLAY_SHORT_DESC_MAX_CHARS} chars" if passed else f"Short description exceeds maximum length ({s_len}/{PLAY_SHORT_DESC_MAX_CHARS} chars)",
                fix=f"Ensure short description is <= {PLAY_SHORT_DESC_MAX_CHARS} chars",
            ))
        else:
            checks.append(ReadinessCheck(
                category="Storefront Listing Copy",
                name="Short Description",
                passed=False,
                severity="WARNING",
                detail="short_description.txt missing from listing directory",
                fix="Create short_description.txt (<= 80 chars)",
            ))

        full_file = listing_dir / "full_description.txt" if (listing_dir / "full_description.txt").is_file() else listing_dir / "full-description.txt"
        if full_file.is_file():
            full_text = full_file.read_text(errors="ignore").strip()
            f_len = len(full_text)
            has_min_len = f_len >= 250
            has_structure = any(kw in full_text.upper() for kw in ("FEATURES", "HOW IT WORKS", "ABOUT", "•", "- "))
            passed = (0 < f_len <= PLAY_FULL_DESC_MAX_CHARS) and has_min_len and has_structure
            if not has_min_len:
                detail = f"Full description is too brief ({f_len} chars); Play policy requires clear feature descriptions (min 250 chars) to prevent rejection"
                severity = "WARNING"
            elif not has_structure:
                detail = f"Full description lacks structured feature sections or bullet points ({f_len} chars)"
                severity = "WARNING"
            elif f_len > PLAY_FULL_DESC_MAX_CHARS:
                detail = f"Full description exceeds limit ({f_len}/{PLAY_FULL_DESC_MAX_CHARS} chars)"
                severity = "BLOCKER"
            else:
                detail = f"Full description length: {f_len}/{PLAY_FULL_DESC_MAX_CHARS} chars (structured)"
                severity = "INFO"
            checks.append(ReadinessCheck(
                category="Storefront Listing Copy",
                name="Full Description",
                passed=passed,
                severity=severity,
                detail=detail,
                fix=f"Provide a structured description with feature bullets (>= 250 and <= {PLAY_FULL_DESC_MAX_CHARS} chars)",
            ))
        else:
            checks.append(ReadinessCheck(
                category="Storefront Listing Copy",
                name="Full Description",
                passed=False,
                severity="WARNING",
                detail="full_description.txt missing from listing directory",
                fix="Create full_description.txt (<= 4000 chars)",
            ))

    # ── 6. Storefront Graphic Assets ──────────────────────────────────────────
    if listing_dir:
        img_dir = listing_dir / "images" if (listing_dir / "images").is_dir() else (listing_dir / "graphics" if (listing_dir / "graphics").is_dir() else listing_dir)
        icon_path = img_dir / "icon.png"
        icon_dims = _png_dimensions(icon_path) if icon_path.is_file() else None
        has_512 = icon_dims == (512, 512)
        checks.append(ReadinessCheck(
            category="Storefront Graphic Assets",
            name="Play Store 512x512 Icon",
            passed=has_512,
            severity="WARNING" if not has_512 else "INFO",
            detail=f"512x512 PNG icon present ({icon_dims[0]}x{icon_dims[1]})" if has_512 else ("Missing images/icon.png" if not icon_dims else f"Icon dimensions are {icon_dims[0]}x{icon_dims[1]}, expected 512x512"),
            fix="Provide 512x512 32-bit PNG icon in images/icon.png",
        ))

        fg_path = img_dir / "featureGraphic.png" if (img_dir / "featureGraphic.png").is_file() else img_dir / "feature-graphic.png"
        fg_dims = _png_dimensions(fg_path) if (fg_path and fg_path.is_file()) else None
        has_1024 = fg_dims == (1024, 500)
        checks.append(ReadinessCheck(
            category="Storefront Graphic Assets",
            name="Play Store Feature Graphic (1024x500)",
            passed=has_1024,
            severity="WARNING" if not has_1024 else "INFO",
            detail=f"1024x500 feature graphic present ({fg_dims[0]}x{fg_dims[1]})" if has_1024 else ("Missing images/featureGraphic.png" if not fg_dims else f"Feature graphic dimensions are {fg_dims[0]}x{fg_dims[1]}, expected 1024x500"),
            fix="Provide 1024x500 PNG/JPEG in images/featureGraphic.png",
        ))

        shots_dir = img_dir / "phoneScreenshots" if (img_dir / "phoneScreenshots").is_dir() else img_dir / "phone-screenshots"
        shots = [f for f in shots_dir.glob("*.png")] + [f for f in shots_dir.glob("*.jpg")] if shots_dir.is_dir() else []
        has_shots = len(shots) >= 2
        checks.append(ReadinessCheck(
            category="Storefront Graphic Assets",
            name="Phone Screenshots (min 2)",
            passed=has_shots,
            severity="WARNING" if not has_shots else "INFO",
            detail=f"{len(shots)} phone screenshots present" if has_shots else f"Found {len(shots)} screenshots (Google Play requires at least 2)",
            fix="Provide at least 2 phone screenshots in images/phoneScreenshots/",
        ))

        # Visual Asset Quality & ASO Conversion Score
        from .asset_critic import review_store_assets
        critic_rep = review_store_assets(src)
        score = critic_rep.get("score", 0)
        grade = critic_rep.get("grade", "F")
        passed_quality = score >= 75
        checks.append(ReadinessCheck(
            category="Storefront Graphic Assets",
            name="Visual Asset Quality & ASO Score",
            passed=passed_quality,
            severity="WARNING" if not passed_quality else "INFO",
            detail=f"Storefront visual assets scored {score}/100 [Grade {grade}]: {critic_rep.get('status')}",
            fix="Elevate visual assets with high-contrast marketing headlines and realistic device bezels via `devteam store-assets <project>`",
        ))

    # ── 7. Localization Completeness ──────────────────────────────────────────
    declared_locales = profile.get("locales", [])
    if declared_locales:
        missing_locales: list[str] = []
        for loc in declared_locales:
            loc_matches = list(src.glob(f"**/res/values-{loc}/strings.xml"))
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

    # ── 8. Release Build & Signing Configuration ──────────────────────────────
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

    # ── 9. Privacy Policy & Public HTTPS Hosting ──────────────────────────────
    conns = profile.get("connections", {})
    privacy_url = (conns.get("play_store", {}).get("privacy_policy_url") or
                   profile.get("privacy_policy_url") or
                   "")
    # Accept any of the conventional names: refusing a document that IS in the
    # repo because it is called privacy-policy.html (not privacy.html) reported a
    # BLOCKER for an app whose policy was committed and published.
    privacy_candidates = [src / "PRIVACY.md"]
    for pattern in ("PRIVACY*.md", "docs/privacy*.md", "docs/PRIVACY*.md", "docs/privacy*.html"):
        privacy_candidates.extend(sorted(src.glob(pattern)))
    has_privacy_doc = any(p.is_file() for p in privacy_candidates)
    checks.append(ReadinessCheck(
        category="Privacy & Policy",
        name="Privacy Policy Document",
        passed=has_privacy_doc,
        severity="BLOCKER" if not has_privacy_doc else "INFO",
        detail="In-repo privacy document present (PRIVACY.md / docs/privacy.html)" if has_privacy_doc else "No privacy document found in repo",
        fix="Create PRIVACY.md documenting zero PII / offline storage practices",
    ))

    is_https = str(privacy_url).strip().startswith("https://")
    url_live = False
    http_error_msg = ""
    if is_https:
        try:
            import urllib.request
            req = urllib.request.Request(
                privacy_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; dev-team/store-readiness)"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.getcode() == 200:
                    url_live = True
        except Exception as e:
            http_error_msg = str(e)

    if not is_https:
        checks.append(ReadinessCheck(
            category="Privacy & Policy",
            name="Public HTTPS Privacy Hosting",
            passed=False,
            severity="WARNING",
            detail="No public https:// privacy policy URL declared in profile.json",
            fix="Run `devteam store-readiness <project> --generate` to host docs/privacy.html via GitHub Pages",
        ))
    elif not url_live:
        checks.append(ReadinessCheck(
            category="Privacy & Policy",
            name="Public HTTPS Privacy Hosting",
            passed=False,
            severity="BLOCKER",
            detail=f"Privacy policy URL returned error or is unreachable: {privacy_url} ({http_error_msg})",
            fix="Ensure GitHub Pages is enabled on the repository or host docs/privacy.html",
        ))
    else:
        checks.append(ReadinessCheck(
            category="Privacy & Policy",
            name="Public HTTPS Privacy Hosting",
            passed=True,
            severity="INFO",
            detail=f"Public HTTPS URL verified live (HTTP 200): {privacy_url}",
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


def store_readiness_dict(project: Path, tree: Path | None = None) -> dict:
    """Return a structured, JSON-serializable dictionary of store readiness audit for researcher / pipeline."""
    checks = run_store_readiness_audit(project, tree=tree)
    blockers = [
        {"category": c.category, "name": c.name, "detail": c.detail, "fix": c.fix}
        for c in checks if not c.passed and c.severity == "BLOCKER"
    ]
    warnings = [
        {"category": c.category, "name": c.name, "detail": c.detail, "fix": c.fix}
        for c in checks if not c.passed and c.severity == "WARNING"
    ]
    passed = [
        {"category": c.category, "name": c.name, "detail": c.detail}
        for c in checks if c.passed
    ]
    is_ready = len(blockers) == 0
    status = "ready" if is_ready and not warnings else ("needs_review" if is_ready else "blocked")

    return {
        "status": status,
        "is_ready_for_submission": is_ready and not warnings,
        "total_checks": len(checks),
        "passed_count": len(passed),
        "blockers_count": len(blockers),
        "warnings_count": len(warnings),
        "blockers": blockers,
        "warnings": warnings,
        "passed_summary": [f"{c['category']}: {c['name']}" for c in passed],
        "checks": [
            {
                "category": c.category,
                "name": c.name,
                "passed": c.passed,
                "severity": c.severity,
                "detail": c.detail,
                "fix": c.fix,
            }
            for c in checks
        ],
    }


def _compile_privacy_html(project: Path, title: str) -> Path:
    privacy_md = project / "PRIVACY.md"
    if not privacy_md.is_file():
        privacy_md = project / "docs/privacy.md"
    if privacy_md.is_file():
        content = privacy_md.read_text(errors="ignore")
    else:
        content = f"# Privacy Policy for {title}\n\n**Last Updated:** {today()}\n\n{title} is an offline-first mobile application. We do not collect, store, transmit, or sell any personally identifiable data."

    html_lines = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("- "):
            html_lines.append(f"<li>{line[2:]}</li>")
        else:
            html_lines.append(f"<p>{line}</p>")
    body_content = "\n".join(html_lines)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — Privacy Policy</title>
  <style>
    :root {{
      --bg: #0A0E14;
      --card: #161F2E;
      --text: #FFFFFF;
      --muted: #8B949E;
      --accent: #00E676;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      margin: 0;
      padding: 24px;
      display: flex;
      justify-content: center;
    }}
    .container {{
      max-width: 760px;
      width: 100%;
      background: var(--card);
      padding: 32px;
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    }}
    h1 {{ color: var(--accent); margin-top: 0; font-size: 28px; }}
    h2 {{ color: var(--text); border-bottom: 1px solid rgba(255, 255, 255, 0.1); padding-bottom: 8px; margin-top: 24px; font-size: 20px; }}
    p, li {{ color: #dfe2eb; font-size: 15px; }}
    li {{ margin-bottom: 8px; }}
    .footer {{ margin-top: 32px; font-size: 13px; color: var(--muted); border-top: 1px solid rgba(255, 255, 255, 0.08); padding-top: 16px; }}
  </style>
</head>
<body>
  <div class="container">
    {body_content}
    <div class="footer">
      Generated automatically by dev-team Play Store Readiness engine.
    </div>
  </div>
</body>
</html>
"""
    out_dir = project / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "privacy.html"
    out_file.write_text(html)
    return out_file


def _compile_landing_html(project: Path, title: str, vision: str) -> Path:
    repo_slug = profile_of(project).get("repo_slug", "")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --bg: #0A0E14;
      --card: #161F2E;
      --text: #FFFFFF;
      --muted: #8B949E;
      --accent: #00E676;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      margin: 0;
      padding: 32px 16px;
      display: flex;
      justify-content: center;
    }}
    .container {{
      max-width: 680px;
      width: 100%;
      background: var(--card);
      padding: 36px;
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    }}
    h1 {{ color: var(--accent); margin-top: 0; font-size: 32px; }}
    p.lead {{ font-size: 18px; color: #dfe2eb; margin-bottom: 24px; }}
    ul {{ list-style: none; padding-left: 0; }}
    li {{ margin-bottom: 12px; padding-left: 28px; position: relative; color: #dfe2eb; font-size: 15px; }}
    li::before {{ content: "✓"; position: absolute; left: 0; color: var(--accent); font-weight: bold; }}
    .actions {{ margin-top: 32px; display: flex; gap: 16px; flex-wrap: wrap; }}
    a.btn {{
      display: inline-block;
      background: var(--accent);
      color: #000;
      font-weight: 600;
      padding: 12px 24px;
      border-radius: 8px;
      text-decoration: none;
    }}
    a.secondary {{
      display: inline-block;
      color: var(--muted);
      padding: 12px 16px;
      text-decoration: underline;
    }}
    .footer {{ margin-top: 40px; font-size: 13px; color: var(--muted); border-top: 1px solid rgba(255, 255, 255, 0.08); padding-top: 16px; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>{title}</h1>
    <p class="lead">{vision or f"A modern, focused mobile utility engineered for speed, reliability, and privacy."}</p>
    <ul>
      <li><strong>Focused Utility:</strong> Purpose-built for quick interactions and glanceable clarity.</li>
      <li><strong>100% Offline-First:</strong> All data remains securely stored on your local device.</li>
      <li><strong>Private & Ad-Free:</strong> Zero account requirements, no tracking, and zero advertisements.</li>
    </ul>
    <div class="actions">
      <a class="btn" href="privacy.html">Read Privacy Policy</a>
      {"<a class='secondary' href='https://github.com/" + repo_slug + "'>GitHub Repository</a>" if repo_slug else ""}
    </div>
    <div class="footer">
      Generated automatically by dev-team Play Store Readiness engine.
    </div>
  </div>
</body>
</html>
"""
    out_dir = project / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "index.html"
    out_file.write_text(html)
    return out_file


def generate_store_readiness_pack(project: Path, log: RunLog | None = None) -> dict:
    """Deterministically generate storefront listing files, hosted privacy HTML,
    and starter visual assets. Zero project names — generic across all apps.
    
    If StitchMCP or external design tools are unavailable in the current agent environment,
    it records an orchestrator escalation request so the orchestrator can synthesize high-fidelity graphics.
    """
    profile = profile_of(project)
    name = profile.get("name") or project.name
    vision = (profile.get("goals") or {}).get("vision", "").strip()

    generated: list[str] = []
    orchestrator_requests: list[dict] = []

    # 1. Compile docs/privacy.html and docs/index.html
    html_path = _compile_privacy_html(project, name)
    generated.append(str(html_path.relative_to(project)))

    index_path = _compile_landing_html(project, name, vision)
    generated.append(str(index_path.relative_to(project)))

    # 2. Wire public HTTPS URL if repo_slug exists and enable GitHub Pages
    repo_slug = profile.get("repo_slug")
    if repo_slug and "/" in repo_slug:
        owner, repo = repo_slug.split("/", 1)
        public_url = f"https://{owner}.github.io/{repo}/privacy.html"
        conns = profile.setdefault("connections", {})
        play_cfg = conns.setdefault("play_store", {})
        if play_cfg.get("privacy_policy_url") != public_url:
            play_cfg["privacy_policy_url"] = public_url
            save_json(project / ".pipeline/profile.json", profile)
            generated.append(".pipeline/profile.json (privacy_policy_url)")

        # Attempt to auto-enable GitHub Pages via gh CLI if available
        try:
            import subprocess
            p_check = subprocess.run(
                ["gh", "api", f"repos/{repo_slug}/pages"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if p_check.returncode != 0:
                payload = json.dumps({"source": {"branch": "main", "path": "/docs"}})
                subprocess.run(
                    ["gh", "api", "--method", "POST", f"repos/{repo_slug}/pages",
                     "-H", "Accept: application/vnd.github+json", "--input", "-"],
                    input=payload,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
        except Exception:
            pass

    # 3. Create distribution/listing/en-US
    listing_dir = project / "distribution/listing/en-US"
    img_dir = listing_dir / "images"
    shots_dir = img_dir / "phoneScreenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    # title.txt (<= 30 chars)
    title_file = listing_dir / "title.txt"
    if not title_file.is_file():
        title_candidate = f"{name}: Scorekeeper" if "score" in vision.lower() else f"{name}"
        title = title_candidate[:PLAY_APP_NAME_MAX_CHARS].rstrip()
        title_file.write_text(title + "\n")
        generated.append(str(title_file.relative_to(project)))

    # short_description.txt (<= 80 chars)
    short_file = listing_dir / "short_description.txt"
    if not short_file.is_file():
        desc = re.split(r"[.—–|]", vision)[0].strip() if vision else "The modern, fast, offline-first mobile app."
        if len(desc) > PLAY_SHORT_DESC_MAX_CHARS:
            desc = desc[:PLAY_SHORT_DESC_MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ,;.")
        short_file.write_text(desc + "\n")
        generated.append(str(short_file.relative_to(project)))

    # full_description.txt (structured, >= 250 and <= 4000 chars)
    full_file = listing_dir / "full_description.txt"
    if not full_file.is_file():
        full_text = (
            f"{name} is a dedicated mobile utility engineered for speed, focus, and reliability.\n\n"
            f"HOW IT WORKS:\n"
            f"1. Launch and interact immediately with high-contrast, glanceable surfaces.\n"
            f"2. Fast one-tap controls designed for quick actions without unnecessary navigation.\n"
            f"3. Instant history and state preservation on your local device.\n\n"
            f"KEY FEATURES:\n"
            f"• Purpose-Built Experience: Clean visual layout designed for quick glances and direct touch controls.\n"
            f"• 100% Offline-First: All data remains securely stored on your device with no cloud requirement.\n"
            f"• Zero Distractions: No account registration, no third-party analytics tracking, and zero ads.\n"
            f"• Fluid & Responsive: Fast interactions with native device feedback.\n\n"
            f"PRIVACY:\n"
            f"{name} does not collect, store, or sell any personal data. All activity is strictly private to your device.\n"
        )
        full_file.write_text(full_text[:PLAY_FULL_DESC_MAX_CHARS])
        generated.append(str(full_file.relative_to(project)))

    # Starter 512x512 icon
    icon_file = img_dir / "icon.png"
    if not icon_file.is_file():
        icon_file.write_bytes(_make_solid_png(512, 512, 10, 14, 20, border_rgb=(0, 230, 118)))
        generated.append(str(icon_file.relative_to(project)))
        orchestrator_requests.append({
            "tool": "StitchMCP / image-generator",
            "action": "Elevate starter 512x512 icon to branded graphic",
            "target": str(icon_file.relative_to(project))
        })

    # Starter 1024x500 feature graphic
    fg_file = img_dir / "featureGraphic.png"
    if not fg_file.is_file():
        fg_file.write_bytes(_make_solid_png(1024, 500, 16, 31, 46, border_rgb=(0, 230, 118)))
        generated.append(str(fg_file.relative_to(project)))
        orchestrator_requests.append({
            "tool": "StitchMCP / image-generator",
            "action": "Elevate starter 1024x500 banner to branded feature graphic",
            "target": str(fg_file.relative_to(project))
        })

    return {
        "generated": generated,
        "summary": f"{len(generated)} storefront items created/updated",
        "orchestrator_requests": orchestrator_requests
    }

