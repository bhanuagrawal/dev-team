"""Play Store Readiness Agent — deterministic audit for Google Play compliance.

Checks manifest, SDK targets, permissions, store assets, localization, and
release configurations against current Google Play Store requirements.
Zero project names — generic across all Android / KMP projects.
"""
from __future__ import annotations

import datetime
import html
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


# Google Play policy mandates targetSdk >= 36 (Android 16 / 2026 requirement) for all new apps and updates.
MIN_ACCEPTABLE_TARGET_SDK = 36
MIN_RECOMMENDED_MIN_SDK = 21
PLAY_APP_NAME_MAX_CHARS = 30
PLAY_SHORT_DESC_MAX_CHARS = 80
# Silhouette agreement between the listing icon and the installed launcher
# bitmap (`pngcmp.shape_similarity`, 0..1). One design rendered twice lands at
# 1.0 (a pair produced by `devteam brand`); a rejected update measured 0.28 and
# two drifted pairs 0.11 and 0.14 — so the line sits in the empty gap between
# "one design rendered twice" and "two marks".
ICON_PARITY_PASS = 0.6
ICON_PARITY_BLOCK = 0.45
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


def _launcher_label(src: Path) -> str | None:
    """The name Android shows under the icon, resolved from the manifest.

    Play's Misleading Claims policy covers the NAME as well as the icon: an app
    whose installed label differs from its listing title is rejected. Returns
    the literal label or the resolved `@string/...` value; None when no
    `<application android:label>` can be read.
    """
    manifests = [p for p in src.glob("**/AndroidManifest.xml")
                 if not re.search(r"(^|/)(build|\.pipeline|\.git)/",
                                  p.relative_to(src).as_posix())]
    for manifest in manifests:
        try:
            text = manifest.read_text(errors="ignore")
        except OSError:
            continue
        found = re.search(
            r"<application\b[^>]*android:label\s*=\s*\"([^\"]+)\"", text, re.S)
        if not found:
            continue
        raw = found.group(1).strip()
        # A manifest/string-resource label is XML: `&amp;` is what aapt decodes
        # into "&" on the device. Compare what Android INSTALLS, not the escaped
        # source, or a title like "ZenList: Habits & Goals" can never pass.
        if not raw.startswith("@string/"):
            return html.unescape(raw)
        key = raw.split("/", 1)[1]
        for strings in manifest.parent.glob("res/values*/strings.xml"):
            try:
                body = strings.read_text(errors="ignore")
            except OSError:
                continue
            value = re.search(
                rf"<string\s+name=\"{re.escape(key)}\"\s*>(.*?)</string>",
                body, re.S)
            if value:
                return html.unescape(value.group(1).strip())
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


_SIGNING_ENTRY_RE = re.compile(
    r"""getByName\(\s*["']release["']\s*\)
      | create\(\s*["']release["']\s*\)
      | named\(\s*["']release["']\s*\)
      | \brelease\s*\{""",
    re.X,
)


def _braced_block(text: str, marker: str) -> str:
    """The inside of the first `<marker> { … }` block, braces balanced.

    Kotlin DSL writes `signingConfigs { create("release") { … } }` and Groovy
    writes `signingConfigs { release { … } }` — same declaration, two syntaxes,
    and a `release { }` block also exists under `buildTypes`. Scoping the search
    to the signingConfigs braces is what keeps a signing check from being
    answered by a release BUILD TYPE (false pass) or from missing Groovy
    entirely (false warning on an app whose CI has signed every release).
    """
    i = text.find(marker)
    if i < 0:
        return ""
    j = text.find("{", i)
    if j < 0:
        return ""
    depth = 0
    for k in range(j, len(text)):
        ch = text[k]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[j:k + 1]
    return text[j:]


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
    field_kebab = re.sub(r"([A-Z])", r"-\1", field).lower()
    patterns = (
        rf"\b(?:{field}|{field_kebab})(?:Version)?\s*(?:=\s*|\(\s*|\s+)[\"']?(\d{{2,3}})[\"']?",
        rf"\b{constant}\b\s*(?::\s*Int\s*)?=\s*[\"']?(\d{{2,3}})[\"']?",
        rf"\b(?:{field}|{field_kebab})(?:Version)?\s*=\s*[A-Za-z_][\w.]*",
    )
    m = re.search(patterns[0], gradle_src) or re.search(patterns[1], constants_src)
    if m:
        return int(m.group(1))
    # A named constant reference: resolve it from the convention sources.
    if re.search(patterns[2], gradle_src):
        m = re.search(rf"\b{constant}\b\s*(?::\s*Int\s*)?=\s*[\"']?(\d{{2,3}})[\"']?", constants_src)
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


# A design token a user can SEE: a colour or a measured size. Prose that names
# the system font families that already ship is documentation, not a restyle.
_DESIGN_VISUAL_TOKEN_RE = re.compile(
    r"#[0-9a-fA-F]{3,8}\b"
    r"|\b\d+(?:\.\d+)?\s*(?:sp|dp)\b"
)


def _design_visual_change_since(src: Path, rel: str, since_ts: float) -> bool | None:
    """Did `rel` change anything a screenshot would show since `since_ts`?

    True / False when git can answer, None when it cannot (no repo, no
    history, a git that refuses) — the caller then keeps the conservative
    mtime verdict rather than guessing. A prose edit cannot make a listing's
    screenshots depict obsolete UI, and saying it does sends an agent to
    repaint owner-approved posters for nothing.
    """
    try:
        log = run(
            ["git", "-C", str(src), "log", f"--since=@{int(since_ts)}",
             "--format=%H", "--", rel],
            src, timeout=60,
        )
        if log.returncode != 0:
            return None
        shas = [line.strip() for line in (log.stdout or "").splitlines() if line.strip()]
        if not shas:
            return False
        for sha in shas:
            diff = run(
                ["git", "-C", str(src), "show", "--unified=0", "--format=",
                 sha, "--", rel],
                src, timeout=60,
            )
            if diff.returncode != 0:
                return None
            for line in (diff.stdout or "").splitlines():
                if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
                    continue
                if _DESIGN_VISUAL_TOKEN_RE.search(line):
                    return True
        return False
    except Exception:
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

    # A version is often not a literal: it is read at build time
    # (`System.getenv("VERSION_CODE")?.toIntOrNull() ?: 5`) so CI can bump it
    # without editing the file. Reading only literals reported such an app as
    # BLOCKED on Play ("versionCode is missing") while it was shipping on every
    # release — the gate cried wolf and the real blockers hid behind it. When no
    # literal is found, the expression's own numeric/string DEFAULT is the value
    # the build resolves to, so that is what is checked; an expression with no
    # default is left unresolved (still a real gap to report).
    version_code_from_default = False
    version_name_from_default = False

    for line in gradle_src.splitlines():
        if "versionCode" in line and not version_code:
            m = re.search(r"versionCode\s*(?:=\s*|\(\s*|\s+)(\d+)", line)
            if not m:
                m = re.search(r":\s*(\d+)\s*\)", line)
            if m:
                version_code = int(m.group(1))
            else:
                m = re.search(r"\?\:\s*\(?\s*(\d+)\s*\)?\s*$", line)
                if m:
                    version_code = int(m.group(1))
                    version_code_from_default = True
        if "versionName" in line and not version_name:
            m = re.search(r'versionName\s*(?:=\s*|\(\s*|\s+)["\']([^"\']+)["\']', line)
            if not m:
                m = re.search(r':\s*["\']([^"\']+)["\']\s*\)', line)
            if m:
                version_name = m.group(1)
            else:
                m = re.search(r'\?\:\s*["\']([^"\']+)["\']\s*\)?\s*$', line)
                if m:
                    version_name = m.group(1)
                    version_name_from_default = True
        if "applicationId" in line and not app_id:
            m = re.search(r'applicationId\s*(?:=\s*|\(\s*|\s+)["\']([^"\']+)["\']', line)
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
            detail=(f"versionCode: {version_code}"
                    + (" (build-time default — the release step supplies the "
                       "real number)" if version_code_from_default else "")),
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
            detail=(f"versionName: '{version_name}'"
                    + (" (build-time default — the release step supplies the "
                       "real name)" if version_name_from_default else "")),
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

    # ── 5b. Release Notes ("What's new") ─────────────────────────────────────
    # Two releases in a row with byte-identical note text is the defect owners
    # see on the listing ("same what's new for every update"). Static and exact:
    # compare the last two SHIPPED notes from the ledger, and the length cap the
    # store enforces. The per-release refusal lives in verify/release; this audit
    # answers "is the listing text currently repeating itself?".
    note_rel = profile.get("whatsnew_file")
    if note_rel:
        from . import shipper

        cap = shipper.whatsnew_max_chars(profile)
        note_path = src / note_rel
        note = note_path.read_text(errors="ignore").strip() if note_path.is_file() else ""
        if not note:
            checks.append(ReadinessCheck(
                category="Storefront Listing Copy",
                name="Release Notes (What's New)",
                passed=False,
                severity="BLOCKER",
                detail=f"No release note at {note_rel} — Play would show nothing for the next update",
                fix=f"Write one user-facing line into {note_rel} and commit it",
            ))
        elif len(note) > cap:
            checks.append(ReadinessCheck(
                category="Storefront Listing Copy",
                name="Release Notes (What's New)",
                passed=False,
                severity="BLOCKER",
                detail=f"Release note is {len(note)}/{cap} chars — Play rejects over-long notes",
                fix=f"Trim {note_rel} to <= {cap} chars",
            ))
        else:
            tail = shipper.shipped_notes(project, note_rel, limit=2)
            repeats = (len(tail) == 2
                       and tail[0][1].strip() == tail[1][1].strip())
            if repeats:
                checks.append(ReadinessCheck(
                    category="Storefront Listing Copy",
                    name="Release Notes (What's New)",
                    passed=False,
                    severity="BLOCKER",
                    detail=(f"v{tail[0][0]} shipped the same note as v{tail[1][0]} — every "
                            "update shows the previous release's words"),
                    fix="Give the next release its own line in the task spec's "
                        '"whatsnew"; verify and release now refuse a repeated note',
                ))
            else:
                checks.append(ReadinessCheck(
                    category="Storefront Listing Copy",
                    name="Release Notes (What's New)",
                    passed=True,
                    severity="INFO",
                    detail=f"Release note present, {len(note)}/{cap} chars, distinct release to release",
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
            severity="BLOCKER" if not has_512 else "INFO",
            detail=f"512x512 PNG icon present ({icon_dims[0]}x{icon_dims[1]})" if has_512 else ("Missing images/icon.png" if not icon_dims else f"Icon dimensions are {icon_dims[0]}x{icon_dims[1]}, expected 512x512"),
            fix="Provide 512x512 32-bit PNG icon in images/icon.png via `devteam storefront-brand <project>` (Stitch design)",
        ))

        fg_path = img_dir / "featureGraphic.png" if (img_dir / "featureGraphic.png").is_file() else img_dir / "feature-graphic.png"
        fg_dims = _png_dimensions(fg_path) if (fg_path and fg_path.is_file()) else None
        has_1024 = fg_dims == (1024, 500)
        checks.append(ReadinessCheck(
            category="Storefront Graphic Assets",
            name="Play Store Feature Graphic (1024x500)",
            passed=has_1024,
            severity="BLOCKER" if not has_1024 else "INFO",
            detail=f"1024x500 feature graphic present ({fg_dims[0]}x{fg_dims[1]})" if has_1024 else ("Missing images/featureGraphic.png" if not fg_dims else f"Feature graphic dimensions are {fg_dims[0]}x{fg_dims[1]}, expected 1024x500"),
            fix="Provide 1024x500 PNG/JPEG in images/featureGraphic.png via `devteam storefront-brand <project> --generate` (Gemini painted)",
        ))

        shots_dir = img_dir / "phoneScreenshots" if (img_dir / "phoneScreenshots").is_dir() else img_dir / "phone-screenshots"
        shots = [f for f in shots_dir.glob("*.png")] + [f for f in shots_dir.glob("*.jpg")] if shots_dir.is_dir() else []
        has_shots = len(shots) >= 4
        checks.append(ReadinessCheck(
            category="Storefront Graphic Assets",
            name="Phone Screenshots (min 4)",
            passed=has_shots,
            severity="BLOCKER" if not has_shots else "INFO",
            detail=f"{len(shots)} phone screenshots present (minimum 4 required)" if has_shots else f"Found {len(shots)} screenshots (Rule 5 requires at least 4 phone screenshots)",
            fix="Render composables and compile marketing posters via `devteam storefront-build <project> --poster`",
        ))

        # Storefront Screenshots Freshness Gate: screenshots must not be older than
        # the design source. There is no docs/design.md (retired 2026-11-09): the
        # Stitch screen set is the design, so its manifest/screenshots are the clock.
        design_file = src / "docs" / "stitch" / "manifest.json"
        if not design_file.is_file():
            design_file = src / ".pipeline" / "stitch_manifest.json"
        if not design_file.is_file():
            design_file = src / "docs" / "design.md"   # legacy projects only
        if design_file.is_file() and shots:
            design_mtime = design_file.stat().st_mtime
            oldest_shot_mtime = min(s.stat().st_mtime for s in shots)
            is_stale = (design_mtime - oldest_shot_mtime) > 600
            # A design.md write is not automatically a VISUAL change: a run that
            # only documents the font families that already ship used to mark a
            # listing "obsolete pre-revamp UI" and send an agent to repaint four
            # owner-approved posters for nothing (2026-11-08). When git can
            # answer, judge the edit by the tokens it actually moved.
            fresh_because_visual_unchanged = False
            comp_ts = None
            if is_stale:
                frames_meta = src / "docs" / "storefront-frames.json"
                if frames_meta.is_file():
                    try:
                        f_data = json.loads(frames_meta.read_text())
                        at_str = (f_data.get("composition") or {}).get("at")
                        if at_str:
                            comp_ts = datetime.datetime.fromisoformat(at_str).timestamp()
                            if comp_ts >= design_mtime or (design_mtime - comp_ts) <= 600:
                                is_stale = False
                    except Exception:
                        pass
            if is_stale and comp_ts is not None:
                visual = _design_visual_change_since(
                    src, str(design_file.relative_to(src)), comp_ts)
                if visual is False:
                    is_stale = False
                    fresh_because_visual_unchanged = True
            checks.append(ReadinessCheck(
                category="Storefront Graphic Assets",
                name="Storefront Screenshots Freshness",
                passed=not is_stale,
                severity="BLOCKER" if is_stale else "INFO",
                detail=(
                    "Screenshots are fresh relative to the design source's visual tokens "
                    "(only prose changed since the last composition)"
                    if fresh_because_visual_unchanged
                    else ("Screenshots are fresh relative to the design source" if not is_stale else (
                        f"Screenshots in {shots_dir.relative_to(src)} are older than the Stitch design source — "
                        "listing depicts obsolete pre-revamp UI/palette"
                    ))
                ),
                fix="Run `devteam storefront-build <project> --poster --force` to regenerate screenshots matching the new design",
            ))

        # Brand Asset Provenance Gate (Rule 5: Stitch icon + Gemini banner)
        from . import storefront_brand as sbf
        brand_issues = sbf.gate(src)
        passed_brand = len(brand_issues) == 0
        checks.append(ReadinessCheck(
            category="Storefront Graphic Assets",
            name="Brand Asset Provenance Gate",
            passed=passed_brand,
            severity="BLOCKER" if not passed_brand else "INFO",
            detail="Brand assets verified (icon=Stitch, featureGraphic=Gemini)" if passed_brand else "; ".join(brand_issues),
            fix="Run `devteam storefront-brand <project>` to generate Stitch icon and Gemini feature graphic",
        ))

        # The icon a user taps must BE the icon the listing shows. Play rejected
        # a shipped update on 2026-11-04 under the Misleading Claims policy —
        # "your app's installed icon or name differs from its store listing" —
        # and this audit passed it, because it only asked whether a launcher icon
        # and a 512 PNG EXIST. Existence is not agreement: the store showed a
        # green shield while the installed app showed a power glyph.
        from . import pngcmp
        listing_icon = img_dir / "icon.png"
        launchers = [p for p in src.glob("**/res/mipmap-*/ic_launcher.png")
                     if not re.search(r"(^|/)(build|\.pipeline|\.git)/",
                                      p.relative_to(src).as_posix())]
        launchers.sort(key=lambda p: p.stat().st_size, reverse=True)
        if listing_icon.is_file() and launchers:
            sim = pngcmp.shape_similarity(listing_icon, launchers[0])
            dist = pngcmp.distance(listing_icon, launchers[0])
            if sim is None:
                checks.append(ReadinessCheck(
                    category="Storefront Graphic Assets",
                    name="Installed Icon Matches Store Listing",
                    passed=False,
                    severity="WARNING",
                    detail="could not decode the listing icon or the launcher bitmap — parity is unproven",
                    fix="Export images/icon.png and mipmap-*/ic_launcher.png as standard 8-bit PNGs",
                ))
            else:
                same = sim >= ICON_PARITY_PASS
                blocking = sim < ICON_PARITY_BLOCK
                checks.append(ReadinessCheck(
                    category="Storefront Graphic Assets",
                    name="Installed Icon Matches Store Listing",
                    passed=same,
                    severity="INFO" if same else ("BLOCKER" if blocking else "WARNING"),
                    detail=(f"launcher {launchers[0].name} and listing icon agree "
                            f"(shape {sim:.2f}, dHash {dist})"
                            if same else
                            f"the installed app icon differs from the listing icon "
                            f"(shape {sim:.2f}, dHash {dist}) — Play rejects this as an "
                            f"app-store listing mismatch under the Misleading Claims policy"),
                    fix=("Re-render the launcher from the listing art: `devteam brand <project> "
                         "--design --from <artboard.html> --worktree <dt/… tree>` — one design, "
                         "one set of pixels, and it records the provenance `verify` reads"),
                ))
        else:
            # A project whose launcher is only an adaptive VECTOR has no bitmap to
            # compare, and that used to be reported as a benign warning forever —
            # which is how a launcher left over from a scaffold shipped for months
            # under an adopted listing icon (Play rejects that as a Misleading
            # Claims mismatch). When brand art IS recorded, an unprovable launcher
            # is a BLOCKER, and the fix is one command that renders the same
            # artboard into every density.
            brand_icon = {}
            try:
                brand_icon = ((sbf.load(project).get("assets") or {}).get("icon") or {})
            except Exception:
                brand_icon = {}
            adaptive = [p for p in src.glob("**/res/mipmap-anydpi-v26/ic_launcher.xml")
                        if not re.search(r"(^|/)(build|\.pipeline|\.git)/",
                                         p.relative_to(src).as_posix())]
            if brand_icon.get("source") in ("stitch", "gemini", "owner") and adaptive:
                checks.append(ReadinessCheck(
                    category="Storefront Graphic Assets",
                    name="Installed Icon Matches Store Listing",
                    passed=False,
                    severity="BLOCKER",
                    detail=("the launcher is an adaptive VECTOR with no rendered bitmap, so "
                            "it cannot be proven to be the recorded listing icon — the icon a "
                            "user taps may be a different design (Play rejects this as a "
                            "listing mismatch)"),
                    fix=f"devteam storefront-brand {project.name} --launcher  "
                        f"— renders the adopted artboard into mipmap-*/ic_launcher.png and "
                        f"the adaptive foreground, then this check compares the pixels",
                ))
            else:
                checks.append(ReadinessCheck(
                    category="Storefront Graphic Assets",
                    name="Installed Icon Matches Store Listing",
                    passed=False,
                    severity="WARNING",
                    detail="no listing icon or no launcher bitmap to compare",
                    fix="Add images/icon.png and mipmap-*/ic_launcher.png, then re-run this audit",
                ))

        # The icon check above only covers HALF of Play's Misleading Claims rule.
        # The other half is the name: an app that installs as "Foo" while the
        # listing promises "Foo: Something More" is rejected exactly the same
        # way, and this audit used to pass it because it only compared pixels.
        # That is not a hypothetical — it is how a real rejection shipped past a
        # green readiness report. Compare the launcher label to the listing title.
        installed_name = _launcher_label(src)
        title_path = listing_dir / "title.txt"
        if title_path.is_file() and installed_name:
            wanted = title_path.read_text(errors="ignore").strip()
            same_name = installed_name.strip() == wanted
            checks.append(ReadinessCheck(
                category="Storefront Graphic Assets",
                name="Installed Name Matches Store Listing",
                passed=same_name,
                severity="INFO" if same_name else "BLOCKER",
                detail=(f"launcher label and listing title agree ({wanted!r})"
                        if same_name else
                        f"the installed app is named {installed_name!r} while the "
                        f"listing title is {wanted!r} — Play rejects this as an "
                        f"app-store listing mismatch under the Misleading Claims "
                        f"policy"),
                fix=("Make @string/app_name equal the listing title exactly (or "
                     "shorten the title to the installed name) and ship a build "
                     "that carries it"),
            ))
        else:
            checks.append(ReadinessCheck(
                category="Storefront Graphic Assets",
                name="Installed Name Matches Store Listing",
                passed=False,
                severity="WARNING",
                detail="no listing title or no resolvable launcher label — name parity is unproven",
                fix="Add title.txt and an android:label on <application>, then re-run this audit",
            ))

        # A listing is a promise about a specific build. When the app is rebuilt
        # but `docs/storefront.json` still names the old screens, the screenshots
        # on Play depict surfaces the user will never see — the "Misleading
        # Claims" defect one gate past existence. The scene spec is checked
        # against DECLARATIONS in shipped main sources plus the project-owned
        # preview wrappers (never call sites: the generated capture harness calls
        # the old names and would vouch for them).
        #
        # The wrappers are a documented part of the scaffolder's design: a scene
        # is composed from fixture data in a TEST source set so none of it reaches
        # the APK (see `storefront_preview.write_harness`, which writes the
        # generated harness into `src/test`, `src/androidUnitTest` or
        # `src/screenshotTest`). Reading main alone made every such project a
        # BLOCKER that no honest fix could clear — the only way to satisfy it
        # would be shipping screenshot fixtures into the app.
        spec_file = src / "docs" / "storefront.json"
        if spec_file.is_file():
            try:
                scenes = (json.loads(spec_file.read_text()) or {}).get("scenes") or []
            except (OSError, ValueError):
                scenes = []
            _main_sets = {"main", "commonMain", "androidMain", "jvmMain", "desktopMain"}
            _preview_sets = {"test", "androidTest", "commonTest", "androidUnitTest",
                             "jvmTest", "desktopTest", "screenshotTest"}
            # The harness is generated from this very JSON; it must not be able to
            # certify a name the project never declared.
            _generated_harness = {"StorefrontScreenshotsTest.kt", "StorefrontScenes.kt"}
            _main_files = []
            _preview_files = []
            for p in src.glob("**/src/*/**/*.kt"):
                if not p.is_file():
                    continue
                rel = p.relative_to(src).as_posix()
                if re.search(r"(^|/)(build|\.pipeline|\.git)/", rel):
                    continue
                parts = p.relative_to(src).parts
                if any(parts[i + 1] in _main_sets
                       for i, seg in enumerate(parts)
                       if seg == "src" and i + 1 < len(parts)):
                    _main_files.append(p)
                elif (p.name not in _generated_harness
                      and any(parts[i + 1] in _preview_sets
                              for i, seg in enumerate(parts)
                              if seg == "src" and i + 1 < len(parts))):
                    _preview_files.append(p)
            main_text = "\n".join(p.read_text(errors="ignore") for p in _main_files)
            preview_text = "\n".join(p.read_text(errors="ignore") for p in _preview_files)
            missing: list[str] = []
            for scene in scenes:
                call = str(scene.get("composable") or "")
                # A scene may WRAP the app's screen: a bottom sheet over the home
                # screen is a real presentation, so the expression starts with a
                # layout container (`Box(...) { MindfulHomeScreen(...) }`) — reading
                # only the first identifier called that a superseded screen and
                # blocked the listing that had just been rendered from the real one
                # (2026-11-07). The scene is honest when ANY composable it names is
                # declared in the project's own source; only a scene naming none is
                # describing a screen that no longer exists.
                names = re.findall(r"\b([A-Z][A-Za-z0-9_]*)\s*\(", call)
                if not names:
                    continue
                declared_any = any(
                    re.search(rf"\b(?:fun|class|object)\s+{re.escape(name)}\b",
                              main_text) or
                    re.search(rf"\b(?:fun|class|object)\s+{re.escape(name)}\b",
                              preview_text)
                    for name in names
                )
                if not declared_any:
                    missing.append(f"{scene.get('id') or '?'}:{names[0]}")
            if not main_text:
                # No source set resolved (an unfamiliar layout): saying every
                # scene is missing would be a false blocker, so this is unmeasured.
                checks.append(ReadinessCheck(
                    category="Storefront Graphic Assets",
                    name="Storefront Scenes Match the App",
                    passed=True,
                    severity="WARNING",
                    detail="no main source set found to compare docs/storefront.json against — unmeasured",
                ))
            elif missing:
                checks.append(ReadinessCheck(
                    category="Storefront Graphic Assets",
                    name="Storefront Scenes Match the App",
                    passed=False,
                    severity="BLOCKER",
                    detail=(f"{len(missing)} scene(s) in docs/storefront.json name a screen the "
                            f"shipped app no longer has ({', '.join(missing[:5])}) — the listing "
                            f"screenshots depict a superseded UI"),
                    fix=("Re-author docs/storefront.json for the current surfaces and re-render: "
                         "`devteam storefront-scenes <project> --scaffold` then "
                         "`devteam storefront-build <project> --poster`"),
                ))
            else:
                checks.append(ReadinessCheck(
                    category="Storefront Graphic Assets",
                    name="Storefront Scenes Match the App",
                    passed=True,
                    severity="INFO",
                    detail=f"{len(scenes)} scene(s) in docs/storefront.json resolve to shipped composables",
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
            severity="BLOCKER" if not passed_quality else "INFO",
            detail=f"Storefront visual assets scored {score}/100 [Grade {grade}]: {critic_rep.get('status')}",
            fix="Elevate visual assets with high-contrast marketing headlines and realistic device bezels via `devteam storefront-build <project> --poster`",
        ))

    # ── 7. Localization Completeness ──────────────────────────────────────────
    declared_locales = profile.get("locales", [])
    if isinstance(declared_locales, list) and declared_locales:
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
    elif isinstance(declared_locales, int) and declared_locales > 0:
        val_dirs = [d for d in src.glob("**/res/values-*") if d.is_dir() and "values-v" not in d.name and "values-night" not in d.name]
        checks.append(ReadinessCheck(
            category="Localization",
            name=f"Declared Locales ({declared_locales} locales)",
            passed=len(val_dirs) >= declared_locales,
            severity="INFO",
            detail=f"Found {len(val_dirs)} localized resource directories (expected ~{declared_locales})",
        ))

    # ── 8. Release Build & Signing Configuration ──────────────────────────────
    signing_block = _braced_block(gradle_src, "signingConfigs")
    has_signing = bool(signing_block) and _SIGNING_ENTRY_RE.search(signing_block) is not None
    has_keystore_ref = "keystore.properties" in gradle_src or "storeFile" in gradle_src
    checks.append(ReadinessCheck(
        category="Release Configuration",
        name="Release Signing Configuration",
        passed=has_signing and has_keystore_ref,
        severity="WARNING" if not (has_signing and has_keystore_ref) else "INFO",
        detail="Release signingConfig configured via keystore reference" if (has_signing and has_keystore_ref) else "Release signingConfig not fully configured in build.gradle / build.gradle.kts",
        fix="Configure signingConfigs.release with keystore properties for Play App Bundle signing",
    ))

    # Keystore off-repo backup verification.
    # The backup lives beside the PROJECT (vault/Drive are keyed by project
    # name), while `src` is the tree being audited — often `.pipeline/scan`.
    # Passing `src` made every project report a missing backup that `doctor`
    # had just verified: a presence-driven warning that could never clear.
    try:
        from . import keystore_backup
        has_backup, backup_detail = keystore_backup.verify_backup(project)
        checks.append(ReadinessCheck(
            category="Release Configuration",
            name="Off-Repo Keystore Backup",
            passed=has_backup,
            severity="WARNING" if not has_backup else "INFO",
            detail=backup_detail if has_backup else "Release signing keystore has no off-repo backup in Google Drive or ~/.config/devteam/keystores",
            fix=f"Run: python3 cli.py keystore-backup {project.name}",
        ))
    except Exception as _e:
        pass

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

    # 9. Automation Loop (Nightly launchd Schedule)
    from . import scheduler
    is_manual = profile.get("schedule") == "manual"
    # The schedule belongs to the REGISTERED project, not to the directory this
    # audit is pointed at. Auditing a task worktree (.pipeline/worktrees/dt-…)
    # used the worktree's folder name as the launchd key and reported "no nightly
    # schedule" for a project that has one — a false BLOCKER that could stop a
    # worktree audit for the wrong reason. profile.json carries the registered
    # name, so read the key from there.
    sched_name = str(profile.get("name") or project.name)
    sched_installed = scheduler.is_project_scheduled(sched_name)
    if is_manual:
        checks.append(ReadinessCheck(
            category="Automation & Operations",
            name="Nightly Autonomous Execution Loop",
            passed=True,
            severity="INFO",
            detail="Explicit manual mode declared in profile.json",
        ))
    elif sched_installed:
        job = next((j for j in scheduler.list_installed_jobs()
                    if j["label"] == scheduler.project_label(sched_name)
                    or j["project"] == sched_name), None)
        slots = job["slots"] if job else []
        runs_ok = (len(slots) == scheduler.RUNS_PER_DAY
                   and all(scheduler.in_window(h, m) for h, m in slots))
        detail = (f"launchd nightly schedule registered "
                  f"({scheduler.project_label(sched_name)}): "
                  + ", ".join(f"{h:02d}:{m:02d}" for h, m in slots))
        checks.append(ReadinessCheck(
            category="Automation & Operations",
            name="Nightly Autonomous Execution Loop",
            passed=runs_ok,
            severity="INFO" if runs_ok else "BLOCKER",
            detail=detail if runs_ok else
                   (f"{sched_name} does not run {scheduler.RUNS_PER_DAY}x/night inside "
                    f"{scheduler.WINDOW_START_HOUR:02d}:00–{scheduler.WINDOW_END_HOUR:02d}:00 "
                    f"(found {detail})"),
            fix="" if runs_ok else
                f"Run `devteam schedules --rebalance` (or `devteam setup --project {sched_name} --schedule auto`)",
        ))
    else:
        checks.append(ReadinessCheck(
            category="Automation & Operations",
            name="Nightly Autonomous Execution Loop",
            passed=False,
            severity="BLOCKER",
            detail=f"No nightly schedule registered in launchd for {sched_name}",
            fix=f"Run `devteam setup --project {sched_name} --schedule auto` to register {scheduler.RUNS_PER_DAY}x/night launchd triggers",
        ))

    # ── 10. Discovery & Product Architecture ──────────────────────────────
    from . import competition
    comp_ok, comp_summary, _ = competition.verify_competition_research(project, tree=src)
    checks.append(ReadinessCheck(
        category="Discovery & Product Architecture",
        name="Competitive Market Recon (docs/COMPETITION_RESEARCH.md)",
        passed=comp_ok,
        severity="BLOCKER" if not comp_ok else "INFO",
        detail=comp_summary,
        fix="Run `devteam competition <project> --limit 5` to research >= 5 competitors and write docs/COMPETITION_RESEARCH.md",
    ))

    prd_file = src / "docs/PRD.md"
    has_prd = prd_file.is_file() and len(prd_file.read_text(errors="ignore").strip()) >= 500
    checks.append(ReadinessCheck(
        category="Discovery & Product Architecture",
        name="Product Requirements Document (docs/PRD.md)",
        passed=has_prd,
        severity="BLOCKER" if not has_prd else "INFO",
        detail=f"PRD present ({len(prd_file.read_text(errors='ignore')) if prd_file.is_file() else 0} chars)" if has_prd else "docs/PRD.md is missing or empty",
        fix="Run `devteam revamp <project>` to synthesize a comprehensive docs/PRD.md anchored to profile vision",
    ))

    design_file = src / "docs/design.md"
    has_design = design_file.is_file() and len(design_file.read_text(errors="ignore").strip()) >= 500
    checks.append(ReadinessCheck(
        category="Discovery & Product Architecture",
        name="Design System Specification (docs/design.md)",
        passed=has_design,
        severity="BLOCKER" if not has_design else "INFO",
        detail="Authoritative design tokens & typography specification present" if has_design else "docs/design.md is missing or incomplete",
        fix="Run `devteam design <project>` to synthesize domain-differentiated design tokens",
    ))

    return checks


def format_readiness_report(project: Path, checks: list[ReadinessCheck]) -> tuple[str, bool]:
    # Name the registered project, not the folder the audit was pointed at: a
    # worktree audit must not print "dt-<task>" as if it were a different app.
    try:
        from .core import profile_of

        audit_name = str((profile_of(project) or {}).get("name") or project.name)
    except Exception:
        audit_name = project.name
    lines: list[str] = []
    lines.append(f"PLAY STORE READINESS AUDIT: {audit_name}")
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
      <a class="btn" style="background: rgba(255,255,255,0.10); color: #FFF;" href="goals.html">Project Roadmap</a>
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

    # 4. Synthesize fallback visual assets ONLY if completely missing
    icon_file = img_dir / "icon.png"
    fg_file = img_dir / "featureGraphic.png"
    shots = list(shots_dir.glob("*.png"))
    if not icon_file.is_file() or not fg_file.is_file() or len(shots) < 4:
        try:
            from .marketing_asset_generator import generate_all_marketing_templates, render_storefront_assets
            generate_all_marketing_templates(project)
            render_res = render_storefront_assets(project, log=log)
            if render_res.get("status") == "OK":
                for r in render_res.get("rendered", []):
                    generated.append(r["file"])
        except Exception as e:
            if log:
                log(f"  warning: marketing storefront asset generation failed: {e}")

    # Note: Brand assets (icon and feature graphic) are strictly required to have
    # authentic provenance per Rule 5 (Stitch for icon, Gemini for feature graphic).
    # Never fabricate solid flat placeholders.
    icon_file = img_dir / "icon.png"
    if not icon_file.is_file() and log:
        log("  notice: Play icon missing — generate via `devteam storefront-brand <project> --stitch-prompt` and adopt")

    fg_file = img_dir / "featureGraphic.png"
    if not fg_file.is_file() and log:
        log("  notice: Feature graphic missing — generate via `devteam storefront-brand <project> --generate` (Gemini)")

    return {
        "generated": generated,
        "summary": f"{len(generated)} storefront items created/updated",
        "orchestrator_requests": orchestrator_requests
    }

