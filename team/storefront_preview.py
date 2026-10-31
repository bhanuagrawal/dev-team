"""Compose Preview Storefront Renderer — photographs the REAL app, not an impression of it.

Why this exists
---------------
The HTML template generator (`marketing_asset_generator`) draws a believable *idea* of
the UI. It cannot know what the app actually looks like, so its screenshots drift from
the product the moment a screen changes — and worse, it seeds colour from `docs/design.md`,
which is a *specification*, not the shipped theme. A store listing that shows a mock is a
deliverable nobody can verify.

This module renders the app's own composables off-device and frames the result:

    scene binding (docs/storefront.json)
        -> @Preview in src/screenshotTest (project-owned Kotlin)
        -> AGP preview screenshot rendering (layoutlib, JVM, no emulator)
        -> <ns>/build/reference PNGs at exactly 1080x2400
        -> marketing frame (1620x3600, 1080-wide device opening, zero resampling)
        -> distribution/listing/en-US/images/phoneScreenshots/

Determinism is the point: no device, no animation clock, no notification shade, no
network images — and the fixture data is explicit, so the shot shows a populated screen
instead of the empty state a device would happen to be in.

Zero project names — generic across all Android / KMP projects.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any

from .core import profile_of, save_json
from .marketing_asset_generator import _find_chrome_executable

SCREENSHOT_PLUGIN_ID = "com.android.compose.screenshot"
SCREENSHOT_PLUGIN_VERSION = "0.0.1-alpha03"  # fallback only; see screenshot_plugin_version()
# The plugin is bound to an AGP range and REJECTS the build outside it ("requires
# AGP version between 8.5.0-beta01 and 8.7"). AGP moves on shipped tasks, so the
# version is chosen from the tree's own AGP rather than a constant.
SCREENSHOT_PLUGIN_BY_AGP: list[tuple[tuple[int, int], str]] = [
    ((8, 9), "0.0.1-alpha16"),   # verified against AGP 8.9.1
    ((8, 6), "0.0.1-alpha09"),
    ((0, 0), "0.0.1-alpha03"),   # verified against AGP 8.5.2
]
UI_TOOLING_FALLBACK = "1.7.5"
COMPOSE_BOM_FALLBACK = "2024.10.01"

# Play phone screenshot canvas, and the marketing frame that contains it 1:1.
# Play rejects any screenshot whose long side is more than twice the short side
# ("The maximum dimension cannot be more than twice the minimum"), so the frame
# canvas stays UNDER 2:1 — a 1620x3600 frame (2.22:1) is refused at upload even
# though it renders beautifully. 1620x3200 = 1.975:1 keeps the same 1080-wide
# opening with zero resampling and room to spare.
SCREEN_W, SCREEN_H = 1080, 2400
FRAME_W, FRAME_H = 1620, 3200
BEZEL_BORDER = 22
BEZEL_W = SCREEN_W + BEZEL_BORDER * 2
BEZEL_X = (FRAME_W - BEZEL_W) // 2
BEZEL_TOP = 740

SPEC_REL = "docs/storefront.json"
MARKER = "devteam storefront-preview"

_SCENE_DEFAULTS: dict[str, list[dict[str, str]]] = {
    "pdf": [
        {"id": "01_hero", "badge": "Desktop-Grade Toolbox",
         "headline": "All-In-One PDF Workstation",
         "sub": "Merge, split, compress and convert documents on your device with instant offline speed."},
        {"id": "02_compress", "badge": "Lossless Compression",
         "headline": "Shrink Files Up To 90%",
         "sub": "Intelligent downsampling cuts file size while keeping vector text razor sharp."},
        {"id": "03_organize", "badge": "Visual Page Canvas",
         "headline": "Drag Pages, Not Paperwork",
         "sub": "Reorder, rotate, split and merge pages with real-time previews."},
        {"id": "04_privacy", "badge": "Local-First Privacy",
         "headline": "Zero Cloud Uploads. Ever.",
         "sub": "Your documents never leave your device."},
    ],
    "court": [
        {"id": "01_hero", "badge": "Core User Loop",
         "headline": "The Screen IS The Scoreboard",
         "sub": "Full-bleed targets engineered for fast tapping without looking."},
        {"id": "02_rules", "badge": "Smart Rules Engine",
         "headline": "Accurate Serving Rotation",
         "sub": "Handles doubles starts, second-server transitions and side-outs automatically."},
        {"id": "03_modes", "badge": "Custom Rules & Modes",
         "headline": "Every Format & Ruleset",
         "sub": "Singles, doubles and tournament play with win-by-2 or sudden death."},
        {"id": "04_stats", "badge": "Post-Match Telemetry",
         "headline": "Deep Performance Stats",
         "sub": "Rally lengths, side-out efficiency, momentum streaks and career wins."},
    ],
    "generic": [
        {"id": "01_hero", "badge": "Core Experience",
         "headline": "Built For Speed",
         "sub": "The essential experience, without the clutter."},
        {"id": "02_detail", "badge": "Every Detail",
         "headline": "Everything In Reach",
         "sub": "A surface for every action you take most often."},
    ],
}

# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
def detect_narrative(project: Path) -> str:
    """Declared narrative wins; keyword detection is only a first-run guess."""
    profile = profile_of(project)
    declared = ((profile.get("storefront") or {}).get("narrative") or "").strip().lower()
    if declared in _SCENE_DEFAULTS:
        return declared
    text = ((profile.get("name") or "") + " " + ((profile.get("goals") or {}).get("vision") or "")).lower()
    if any(k in text for k in ("pdf", "document", "scanner", "office")):
        return "pdf"
    if any(k in text for k in ("pickleball", "scoreboard", "tennis", "court", "padel")):
        return "court"
    return "generic"


def find_compose_module(project: Path) -> Path:
    """The Gradle module that owns the composables (composeApp, app, …)."""
    profile = profile_of(project)
    declared = (profile.get("build") or {}).get("module")
    candidates = [declared] if declared else []
    candidates += ["composeApp", "app", "androidApp", "shared"]
    for name in candidates:
        if not name:
            continue
        d = project / name
        if (d / "build.gradle.kts").is_file() or (d / "build.gradle").is_file():
            return d
    raise SystemExit(f"no compose module found in {project} (looked for {', '.join(candidates)})")


def _build_file(module: Path) -> Path:
    kts = module / "build.gradle.kts"
    return kts if kts.is_file() else module / "build.gradle"


def _module_namespace(module: Path) -> str:
    text = _build_file(module).read_text(errors="ignore")
    m = re.search(r'namespace\s*=\s*"([\w.]+)"', text)
    if m:
        return m.group(1)
    main = module / "src/main"
    for f in main.rglob("*.kt"):
        pkg = re.search(r"^package\s+([\w.]+)", f.read_text(errors="ignore"), re.M)
        if pkg:
            return pkg.group(1)
    return "storefront"


def screenshot_plugin_version(project: Path) -> str:
    """The plugin version that matches this tree's Android Gradle Plugin."""
    major_minor = _agp_version(project)
    for (maj, minor), version in SCREENSHOT_PLUGIN_BY_AGP:
        if major_minor >= (maj, minor):
            return version
    return SCREENSHOT_PLUGIN_VERSION


def _agp_version(project: Path) -> tuple[int, int]:
    """AGP version from the version catalog or the plugins block (0,0 if unknown)."""
    for rel in ("gradle/libs.versions.toml", "build.gradle.kts", "build.gradle"):
        f = project / rel
        if not f.is_file():
            continue
        text = f.read_text(errors="ignore")
        m = re.search(r'^agp\s*=\s*"(\d+)\.(\d+)', text, re.M)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.search(r'com\.android\.(?:application|library)"?\)?\s*version\s*"(\d+)\.(\d+)', text)
        if m:
            return int(m.group(1)), int(m.group(2))
    return (0, 0)


def _compose_ui_tooling_version(project: Path) -> str:
    for rel in ("gradle/libs.versions.toml", "build.gradle.kts", "build.gradle"):
        f = project / rel
        if not f.is_file():
            continue
        m = re.search(r'compose-bom\s*=\s*"([^"]+)"', f.read_text(errors="ignore"))
        if m:
            return m.group(1)
    return COMPOSE_BOM_FALLBACK


def _theme_composable(project: Path, module: Path) -> str | None:
    """Find the app's theme wrapper so a scene renders with shipped colours, not defaults."""
    declared = (profile_of(project).get("storefront") or {}).get("theme")
    if declared:
        return declared
    for f in (module / "src").rglob("*Theme.kt"):
        m = re.search(r"^fun\s+(\w*[Tt]heme)\s*\(\s*content", f.read_text(errors="ignore"), re.M)
        if m:
            return m.group(1)
    return None


# --------------------------------------------------------------------------- #
# docs/storefront.json — the copy + bindings, owned by the project
# --------------------------------------------------------------------------- #
def load_spec(project: Path, create: bool = True, save: bool = True) -> dict[str, Any]:
    spec_file = project / SPEC_REL
    if spec_file.is_file():
        return json.loads(spec_file.read_text())

    if not create:
        raise SystemExit(f"{SPEC_REL} missing — run: devteam storefront-scenes <project> --scaffold")

    profile = profile_of(project)
    module = find_compose_module(project)
    narrative = detect_narrative(project)
    spec = {
        "_comment": "Storefront scene copy. This file is a DELIVERABLE: edit it freely, "
                    "'--scaffold' regenerates the Kotlin harness from it.",
        "narrative": narrative,
        "module": module.name,
        "package": f"{_module_namespace(module)}.storefront",
        "theme": _theme_composable(project, module),
        "renderer": renderer_of(project),
        "imports": [],
        "fixtures": "object StorefrontFixtures {\n    // TODO: impressive, domain-true sample data (no lorem/test/Item 1)\n}",
        "scenes": [dict(s, composable="") for s in _SCENE_DEFAULTS[narrative]],
    }
    if not save:
        return spec
    save_json(spec_file, spec)

    # Make the guess auditable and correctable instead of silent.
    if not (profile.get("storefront") or {}).get("narrative"):
        profile.setdefault("storefront", {})["narrative"] = narrative
        save_json(project / ".pipeline/profile.json", profile)
    return spec


def unbound_scenes(spec: dict[str, Any]) -> list[str]:
    return [s["id"] for s in spec.get("scenes", []) if not (s.get("composable") or "").strip()]


# --------------------------------------------------------------------------- #
# Gradle wiring (idempotent, marker-based)
# --------------------------------------------------------------------------- #
def _insert_plugin_alias(text: str, alias: str) -> str:
    if f"libs.plugins.screenshot" in text or SCREENSHOT_PLUGIN_ID in text:
        return text
    m = re.search(r"plugins\s*\{", text)
    if not m:
        raise SystemExit("no `plugins { }` block in module build file")
    depth, i = 0, m.end()
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            if depth == 0:
                return text[:i] + f"    {alias}\n" + text[i:]
            depth -= 1
        i += 1
    raise SystemExit("unterminated plugins block")


def _insert_after(text: str, pattern: str, insertion: str) -> str:
    m = re.search(pattern, text, re.M)
    if not m:
        # A silent no-op here produces a build.gradle.kts that references a plugin
        # the catalog never declared — the failure then surfaces 30s later as a
        # cryptic "Unresolved reference". Say it at the source instead.
        raise SystemExit(f"cannot find anchor to insert {insertion.strip()[:60]!r} after: "
                         f"pattern {pattern!r} missing")
    end = text.index("\n", m.end()) + 1 if "\n" in text[m.end():] else len(text)
    return text[:end] + insertion + text[end:]


def ensure_tooling(project: Path, module: Path, dry_run: bool = False) -> list[str]:
    """Wire the AGP preview-screenshot plugin into the module. Safe to re-run."""
    changed: list[str] = []
    build = _build_file(module)
    text = build.read_text()
    original = text

    # 1. plugin — via the project's version catalog when it has one.
    wanted = screenshot_plugin_version(project)
    catalog = project / "gradle/libs.versions.toml"
    if catalog.is_file():
        cat = catalog.read_text()
        if SCREENSHOT_PLUGIN_ID not in cat:
            cat = _insert_after(
                cat, r"^\[plugins\]\s*$",
                f'screenshot = {{ id = "{SCREENSHOT_PLUGIN_ID}", version = "{wanted}" }}\n',
            )
            if not dry_run:
                catalog.write_text(cat)
            changed.append(str(catalog.relative_to(project)) + f" (plugin {wanted})")
        else:
            # A stale version in an existing catalog entry is worse than no entry:
            # it fails inside Gradle with "requires AGP version between …".
            m = re.search(rf'({re.escape(SCREENSHOT_PLUGIN_ID)}",\s*version = ")([^"]+)(")', cat)
            if m and m.group(2) != wanted:
                cat = cat[:m.start(2)] + wanted + cat[m.end(2):]
                if not dry_run:
                    catalog.write_text(cat)
                changed.append(str(catalog.relative_to(project)) + f" (plugin {m.group(2)} -> {wanted})")
        text = _insert_plugin_alias(text, "alias(libs.plugins.screenshot)")
    else:
        m = re.search(r"plugins\s*\{", text)
        if m and SCREENSHOT_PLUGIN_ID not in text:
            text = text[:m.end()] + f'\n    id("{SCREENSHOT_PLUGIN_ID}") version "{wanted}"' + text[m.end():]

    # 2. flag that enables the screenshotTest source set (plugin alpha requirement)
    if "android.experimental.enableScreenshotTest" not in text:
        text = _insert_after(
            text, r"^android\s*\{",
            '    experimentalProperties["android.experimental.enableScreenshotTest"] = true\n',
        )
    gp = project / "gradle.properties"
    gp_text = gp.read_text() if gp.is_file() else ""
    if "android.experimental.enableScreenshotTest" not in gp_text:
        if not dry_run:
            gp.write_text(gp_text.rstrip("\n") + "\nandroid.experimental.enableScreenshotTest=true\n")
        changed.append("gradle.properties (flag)")

    # 3. ui-tooling on the screenshotTest configuration, pinned to the app's Compose BOM
    if MARKER not in text and "screenshotTestImplementation" not in text:
        bom = _compose_ui_tooling_version(project)
        block = (
            f"    // {MARKER}: renders real composables off-device (layoutlib)\n"
            f'    screenshotTestImplementation(platform("androidx.compose:compose-bom:{bom}"))\n'
            f'    screenshotTestImplementation("androidx.compose.ui:ui-tooling")\n'
        )
        m = re.search(r"^dependencies\s*\{", text, re.M)
        if not m:
            text = text.rstrip("\n") + f"\n\ndependencies {{\n{block}}}\n"
        else:
            depth, i = 0, m.end()
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    if depth == 0:
                        text = text[:i] + block + text[i:]
                        break
                    depth -= 1
                i += 1

    if text != original:
        if not dry_run:
            build.write_text(text)
        changed.append(str(build.relative_to(project)))
    return changed


# --------------------------------------------------------------------------- #
# Kotlin harness generation
# --------------------------------------------------------------------------- #
def _san(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def renderer_of(project: Path) -> str:
    """Which renderer this module can actually run.

    `agp-preview` needs AGP's preview-screenshot plugin, which only wires the
    screenshotTest variant on the AGP 8.5-8.7 line (KMP androidTarget on 8.9 fails
    with "Required value was null"). A module carrying Roborazzi renders on the JVM
    through Robolectric instead — no device, no plugin, any AGP.
    """
    build = _build_file(project / _module_name(project))
    try:
        text = build.read_text(errors="ignore") + (project / "gradle/libs.versions.toml").read_text(errors="ignore")
    except Exception:
        return "agp-preview"
    if "roborazzi" in text.lower():
        return "roborazzi"
    # Compose test + decorView draw: works on any AGP and any Kotlin version, at the
    # cost of one hand-written capture helper. Right answer for Kotlin 1.x modules.
    if "robolectric" in text.lower() and "ui-test-junit4" in text.lower():
        return "robolectric-capture"
    return "agp-preview"


def _module_name(project: Path) -> str:
    try:
        return find_compose_module(project).name
    except SystemExit:
        return "composeApp"


def roborazzi_source(spec: dict[str, Any]) -> str:
    """A JUnit test that renders each scene to PNG — the renderer-agnostic path."""
    pkg = spec.get("package") or "storefront"
    theme = spec.get("theme")
    imports = "\n".join(f"import {i}" for i in (spec.get("imports") or []))
    fixtures = (spec.get("fixtures") or "").strip() or "object StorefrontFixtures"

    out = [
        f"// GENERATED by {MARKER} from {SPEC_REL} — edit the JSON, not this file.",
        f"package {pkg}",
        "",
        "import androidx.compose.material3.MaterialTheme",
        "import androidx.compose.material3.Surface",
        "import androidx.compose.runtime.Composable",
        "import androidx.compose.ui.test.junit4.createComposeRule",
        "import androidx.compose.ui.test.onRoot",
        "import com.github.takahirom.roborazzi.captureRoboImage",
        "import org.junit.Rule",
        "import org.junit.Test",
        "import org.junit.runner.RunWith",
        "import org.robolectric.RobolectricTestRunner",
        "import org.robolectric.annotation.Config",
        "import org.robolectric.annotation.GraphicsMode",
    ]
    if imports:
        out.append(imports)
    out += [
        "",
        "/** Renders the Play phone screenshots from the app's own composables (JVM). */",
        "@RunWith(RobolectricTestRunner::class)",
        "@GraphicsMode(GraphicsMode.Mode.NATIVE)",
        # android.app.Application, not the project's own Application class: a scene is
        # a composable with fixture data, and a real Application.onCreate (WorkManager,
        # Firebase, DI graphs) turns a screenshot run into an app-boot test that fails
        # for reasons the screenshot does not depend on.
        '@Config(sdk = [34], qualifiers = "w392dp-h872dp-440dpi", '
        "application = android.app.Application::class)",
        "class StorefrontScreenshotsTest {",
        "",
        "    @get:Rule",
        "    val compose = createComposeRule()",
        "",
        "    private fun scene(name: String, content: @Composable () -> Unit) {",
        # A scene must sit on the app's own surface: several screens inherit their
        # background from the host, and a transparent scene renders light text on the
        # renderer's white canvas (unreadable screenshots, not a design bug).
        "        compose.setContent { "
        + (f"{theme} {{ " if theme else "")
        + "Surface(color = MaterialTheme.colorScheme.background) { content() }"
        + (" }" if theme else "")
        + " }",
        '        val dir = System.getProperty("storefront.output") ?: "build/storefront"',
        '        java.io.File(dir).mkdirs()',
        '        compose.onRoot().captureRoboImage("$dir/$name.png")',
        "    }",
        "",
        fixtures,
        "",
    ]
    for scene in spec.get("scenes", []):
        body = (scene.get("composable") or "").strip()
        out += [
            f'    @Test',
            f'    fun scene_{_san(scene["id"])}() = scene("{scene["id"]}") {{',
            f"        {body}",
            "    }",
            "",
        ]
    out.append("}")
    return "\n".join(out)


def robolectric_source(spec: dict[str, Any]) -> str:
    """Compose test that draws the decor view into a PNG — no screenshot library.

    Compose's `captureToImage()` waits for a real redraw, which Robolectric never
    performs (it times out after 2 s); drawing the decor view is the honest
    equivalent and needs only Robolectric + ui-test-junit4.
    """
    pkg = spec.get("package") or "storefront"
    theme = spec.get("theme")
    imports = "\n".join(f"import {i}" for i in (spec.get("imports") or []))
    fixtures = (spec.get("fixtures") or "").strip() or "object StorefrontFixtures"

    out = [
        f"// GENERATED by {MARKER} from {SPEC_REL} — edit the JSON, not this file.",
        f"package {pkg}",
        "",
        "import android.graphics.Bitmap",
        "import android.graphics.Canvas",
        "import androidx.activity.ComponentActivity",
        "import androidx.compose.runtime.Composable",
        "import androidx.compose.ui.test.junit4.createAndroidComposeRule",
        "import org.junit.Rule",
        "import org.junit.Test",
        "import org.junit.runner.RunWith",
        "import org.robolectric.RobolectricTestRunner",
        "import org.robolectric.annotation.Config",
        "import org.robolectric.annotation.GraphicsMode",
        "import java.io.File",
        "import java.io.FileOutputStream",
    ]
    if imports:
        out.append(imports)
    out += [
        "",
        "/** Renders the Play phone screenshots from the app's own composables (JVM). */",
        "@RunWith(RobolectricTestRunner::class)",
        "@GraphicsMode(GraphicsMode.Mode.NATIVE)",
        '@Config(sdk = [34], qualifiers = "w392dp-h872dp-440dpi")',
        "class StorefrontScreenshotsTest {",
        "",
        "    @get:Rule",
        "    val compose = createAndroidComposeRule<ComponentActivity>()",
        "",
        "    private fun scene(name: String, content: @Composable () -> Unit) {",
        "        compose.setContent { " + (f"{theme} {{ content() }}" if theme else "content()") + " }",
        "        compose.waitForIdle()",
        "        val view = compose.activity.window.decorView",
        "        val bitmap = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888)",
        "        view.draw(Canvas(bitmap))",
        '        val dir = File(System.getProperty("storefront.output") ?: "build/storefront")',
        "        dir.mkdirs()",
        '        FileOutputStream(File(dir, "$name.png")).use {',
        "            bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)",
        "        }",
        "    }",
        "",
        fixtures,
        "",
    ]
    for scene in spec.get("scenes", []):
        body = (scene.get("composable") or "").strip()
        out += [
            "    @Test",
            f'    fun scene_{_san(scene["id"])}() = scene("{scene["id"]}") {{',
            f"        {body}",
            "    }",
            "",
        ]
    out.append("}")
    return "\n".join(out)


def harness_source(spec: dict[str, Any], ns: str | None = None) -> str:
    if spec.get("renderer") in ("roborazzi", "robolectric-capture"):
        return (roborazzi_source(spec) if spec["renderer"] == "roborazzi"
                else robolectric_source(spec))
    pkg = spec.get("package") or "storefront"
    theme = spec.get("theme")
    imports = "\n".join(f"import {i}" for i in (spec.get("imports") or []))
    fixtures = (spec.get("fixtures") or "").strip() or "object StorefrontFixtures"

    out = [
        f"// GENERATED by {MARKER} from {SPEC_REL} — edit the JSON, not this file.",
        f"package {pkg}",
        "",
        "import androidx.compose.material3.MaterialTheme",
        "import androidx.compose.material3.Surface",
        "import androidx.compose.runtime.Composable",
        "import androidx.compose.ui.tooling.preview.Preview",
    ]
    if imports:
        out.append(imports)
    out += [
        "",
        "/** Exactly the Play Store phone screenshot size: 1080x2400 @ 440dpi. */",
        'private const val PHONE = "spec:width=1080px,height=2400px,dpi=440"',
        "",
        fixtures,
        "",
    ]
    for scene in spec.get("scenes", []):
        sid = scene["id"]
        body = (scene.get("composable") or "").strip()
        wrapped = (f"Surface(color = MaterialTheme.colorScheme.background) {{ {body} }}")
        inner = f"{theme} {{\n        {wrapped}\n    }}" if theme else f"    {wrapped}"
        out += [
            f'@Preview(name = "{sid}", device = PHONE, showBackground = true)',
            "@Composable",
            f"fun Scene_{_san(sid)}() {{",
            f"    {inner}",
            "}",
            "",
        ]
    return "\n".join(out)


def write_harness(project: Path, spec: dict[str, Any], force: bool = False, dry_run: bool = False) -> Path:
    """The Kotlin harness is GENERATED — the JSON is the source of truth, so it always syncs."""
    module = project / spec.get("module", find_compose_module(project).name)
    pkg_path = (spec.get("package") or "storefront").replace(".", "/")
    robo = spec.get("renderer") in ("roborazzi", "robolectric-capture")
    name = "StorefrontScreenshotsTest.kt" if robo else "StorefrontScenes.kt"
    if robo:
        # KMP modules put unit tests in `androidUnitTest`; a plain Android module
        # (Groovy or KTS) uses `src/test/java`, and a file in the wrong tree is
        # silently never compiled — it "passes" because it never ran.
        build_text = _build_file(module).read_text(errors="ignore")
        kmp = re.search(r"^\s*kotlin\s*\{", build_text, re.M) or "kotlinMultiplatform" in build_text
        src_dir = module / ("src/androidUnitTest/kotlin" if kmp else "src/test/java")
    else:
        src_dir = module / "src/screenshotTest/kotlin"
    target = src_dir / pkg_path / name

    bound = [s for s in spec.get("scenes", []) if (s.get("composable") or "").strip()]
    if not bound:
        return target  # nothing bindable yet; render refuses with the unbound list

    source = harness_source({**spec, "scenes": bound})
    if target.is_file() and target.read_text() == source:
        return target
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    return target


# --------------------------------------------------------------------------- #
# render + collect
# --------------------------------------------------------------------------- #
def _gradlew(project: Path) -> str | None:
    w = project / "gradlew"
    return str(w) if w.is_file() else (shutil.which("gradle") or None)


def variant_cap(project: Path) -> str | None:
    """The product-flavor cap used by Gradle task names, from the project's own
    verify commands (`:mod:compile<Flavor>DebugKotlinAndroid` -> `<Flavor>`).

    A multi-flavor module has NO `updateDebugScreenshotTest`: Gradle rejects the
    unqualified name as ambiguous ("Candidates are: 'updateZencoach…',
    'update<Flavor>…'"), so the renderer must name the variant it renders. The
    profile already declares that variant for compile/test — the same one every
    gate runs — so the screenshots describe the build the pipeline verifies,
    instead of whichever candidate happens to sort first.
    """
    verify = profile_of(project).get("verify") or {}
    for cmd in (verify.get("compile"), verify.get("test")):
        m = re.search(r"compile([A-Z]\w*?)Debug", str(cmd or ""))
        if m:
            return m.group(1)
        m = re.search(r"test([A-Z]\w*?)Debug", str(cmd or ""))
        if m:
            return m.group(1)
    return None


def _delivery_tree(project: Path, log: Any = None) -> Path:
    """A worktree at origin/main — the tree CI builds, i.e. what users receive.

    Rendering the checkout instead is how screenshots once described code Play
    would never get (the checkout was 68 commits behind). The tree is also given
    the project-owned spec + generated harness, since those are dev-team outputs.
    """
    from .scan import origin_tree
    tree = origin_tree(project, log)
    local_props = project / "local.properties"
    if local_props.is_file() and not (tree / "local.properties").is_file():
        shutil.copy2(local_props, tree / "local.properties")  # gitignored, needed by AGP
    if not (tree / SPEC_REL).is_file() and (project / SPEC_REL).is_file():
        shutil.copy2(project / SPEC_REL, tree / SPEC_REL)
    return tree


def render_previews(project: Path, log: Any = None, timeout: int = 900,
                    tree: Path | None = None) -> dict[str, Any]:
    """Run AGP's layoutlib renderer and collect one PNG per scene.

    Renders the DELIVERY tree by default: a screenshot is a claim about the app,
    so it must come from the code CI ships, never from a stale checkout.
    """
    if tree is None:
        tree = _delivery_tree(project, log)
    if log:
        from .core import delivery_notice
        log("  " + delivery_notice(project))
        log(f"  rendering {tree.relative_to(project) if tree.is_relative_to(project) else tree}")

    spec = load_spec(tree) if (tree / SPEC_REL).is_file() else load_spec(project)
    missing = unbound_scenes(spec)
    if missing:
        return {
            "status": "UNBOUND",
            "error": "scenes not bound to a composable in " + SPEC_REL,
            "missing": missing,
            "hint": "set \"composable\" for each scene (e.g. \"CompressScreen(data = CompressUIData(...), ...)\")",
        }
    spec["module"] = spec.get("module") or find_compose_module(tree).name
    spec["renderer"] = spec.get("renderer") or renderer_of(tree)
    if log:
        log(f"  renderer: {spec['renderer']}")
    write_harness(tree, spec)
    # The tree is disposable and may not carry the preview tooling yet (the wiring
    # can live only in an unpushed branch). Wiring it HERE keeps "render what ships"
    # true even then — the alternative is a cryptic "task not found".
    module_dir = tree / spec["module"]
    if spec.get("renderer") != "agp-preview":
        wired = []
    else:
        wired = ensure_tooling(tree, module_dir)
    if wired and log:
        log("  + wired preview tooling in the delivery tree: " + ", ".join(wired))

    gw = _gradlew(tree)
    if not gw:
        return {"status": "ERROR", "error": "no gradlew and no gradle on PATH"}

    module = spec.get("module") or find_compose_module(tree).name
    cap = variant_cap(project)
    variant = f"{cap}Debug" if cap else "Debug"
    if spec.get("renderer") in ("roborazzi", "robolectric-capture"):
        pkg = spec.get("package") or "storefront"
        out_dir_tree = tree / ".pipeline/storefront_scenes"
        task = f":{module}:test{variant}UnitTest"
        args = [gw, task, "--tests", f"{pkg}.StorefrontScreenshotsTest",
                "-Proborazzi.test.record=true", "--no-configuration-cache", "--console=plain", "-q"]
    else:
        task = f":{module}:update{variant}ScreenshotTest"
        args = [gw, task, "--no-configuration-cache", "--console=plain", "-q"]
    if log:
        log(f"  ~ {task}")
    try:
        res = subprocess.run(args, cwd=tree, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "ERROR", "error": f"{task} timed out after {timeout}s"}

    if res.returncode != 0:
        blob = (res.stderr or "") + (res.stdout or "")
        if "Required value was null" in blob and "compileDebugScreenshotTestKotlin" in blob:
            return {
                "status": "UNSUPPORTED",
                "error": ("AGP's preview-screenshot plugin cannot compile the screenshotTest "
                          "source set for this module (known: KMP androidTarget on AGP >= 8.8 — "
                          "the plugin only wires the variant on the AGP 8.5-8.7 line)"),
                "hint": ("screenshots were NOT generated; do not ship the previous frames as if "
                         "they described this build. Render from a compatible checkout with "
                         "`--from-checkout` (only when it is not stale — see `devteam status`), "
                         "or move the harness to Roborazzi/Robolectric in androidUnitTest."),
            }
        return {
            "status": "ERROR",
            "error": f"{task} failed",
            "stdout": (res.stdout or "")[-3000:],
            "stderr": (res.stderr or "")[-3000:],
        }

    if spec.get("renderer") in ("roborazzi", "robolectric-capture"):
        reference = tree / ".pipeline/storefront_scenes"
    else:
        reference = tree / module / "build/reference"
        if not reference.is_dir():
            reference = tree / module / "src/debug/screenshotTest/reference"
    pngs = sorted(reference.rglob("*.png"))
    if not pngs:
        return {"status": "ERROR", "error": f"no reference PNGs under {reference}"}

    out_dir = project / ".pipeline/storefront_scenes"
    out_dir.mkdir(parents=True, exist_ok=True)
    collected, unknown = [], []
    for scene in spec.get("scenes", []):
        sid = scene["id"]
        match = next((p for p in pngs if p.stem == sid or f"_{sid}_" in p.name), None)
        if not match:
            unknown.append(sid)
            continue
        dest = out_dir / f"{sid}.png"
        if match.resolve() != dest.resolve():
            shutil.copy2(match, dest)
        collected.append({"id": sid, "file": str(dest.relative_to(project)),
                          "bytes": dest.stat().st_size, "source": str(match.relative_to(project))})

    from .core import DELIVERY_REF, git_ref_state
    ref_state = git_ref_state(project)
    manifest = {
        "provenance": "compose_preview" if collected else "none",
        "renderer": SCREENSHOT_PLUGIN_ID,
        "version": SCREENSHOT_PLUGIN_VERSION,
        "rendered_from": {
            "tree": "origin-main" if "scan" in tree.parts else "checkout",
            "ref": DELIVERY_REF,
            "ref_sha": ref_state.get("ref_sha"),
            "stale_checkout": ref_state.get("stale"),
            "checkout_behind": ref_state.get("behind"),
        },
        "scenes": collected,
        "unresolved": unknown,
    }
    save_json(out_dir / "manifest.json", manifest)
    if log:
        for c in collected:
            log(f"  ✓ {c['id']} ({round(c['bytes'] / 1024, 1)} KB)")
        for u in unknown:
            log(f"  ! scene '{u}' produced no PNG")
    return {"status": "OK", **manifest}


# --------------------------------------------------------------------------- #
# marketing frame: 1620x3200 canvas (under Play's 2:1 cap), 1080-wide opening
# --------------------------------------------------------------------------- #
def _frame_html(scene: dict[str, Any], img_uri: str, colors: dict[str, str]) -> str:
    acc = colors.get("primary", "#00E5FF")
    sec = colors.get("secondary", "#7C4DFF")
    badge = scene.get("badge", "")
    headline = scene.get("headline", "")
    sub = scene.get("sub", "")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@700&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ width: {FRAME_W}px; height: {FRAME_H}px; overflow: hidden; position: relative;
         background: radial-gradient(circle at 50% 10%, #16263B 0%, {colors.get('canvas', '#0A0E14')} 62%);
         font-family: 'Inter', sans-serif; color: #fff; }}
  .aura {{ position: absolute; top: 520px; left: 50%; transform: translateX(-50%);
           width: 1200px; height: 1200px;
           background: radial-gradient(circle, {acc}26 0%, {sec}1a 45%, transparent 70%);
           filter: blur(110px); }}
  .header {{ position: absolute; top: 120px; left: 90px; right: 90px; text-align: center; }}
  .badge {{ display: inline-flex; align-items: center; gap: 14px; font-family: 'JetBrains Mono', monospace;
            font-size: 26px; font-weight: 700; letter-spacing: 3px; text-transform: uppercase;
            color: {acc}; background: {acc}1a; border: 2px solid {acc}66;
            padding: 12px 34px; border-radius: 999px; }}
  .dot {{ width: 14px; height: 14px; border-radius: 50%; background: {acc}; box-shadow: 0 0 14px {acc}; }}
  h1 {{ font-family: 'Space Grotesk', sans-serif; font-size: 88px; font-weight: 700; line-height: 1.05;
        letter-spacing: -1px; text-transform: uppercase; margin: 34px 0 22px;
        background: linear-gradient(180deg, #FFFFFF 0%, #D8E2F0 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
  p {{ font-size: 36px; line-height: 1.3; color: {colors.get('text_muted', '#94A3B8')}; max-width: 1320px; margin: 0 auto; }}
  .bezel {{ position: absolute; top: {BEZEL_TOP}px; left: {BEZEL_X}px;
            width: {BEZEL_W}px; height: {FRAME_H - BEZEL_TOP + 60}px;
            background: #151D2A; border: 4px solid #2B3A50; border-bottom: none;
            border-radius: 104px 104px 0 0;
            box-shadow: 0 -30px 90px rgba(0,0,0,0.85), 0 0 130px {acc}33; }}
  .screen {{ position: absolute; top: {BEZEL_BORDER}px; left: {BEZEL_BORDER}px;
             width: {SCREEN_W}px; height: {SCREEN_H}px; overflow: hidden;
             border-radius: 80px 80px 0 0; background: #000; }}
  .screen img {{ display: block; width: {SCREEN_W}px; height: {SCREEN_H}px; }}
</style></head>
<body>
  <div class="aura"></div>
  <div class="header">
    <div class="badge"><div class="dot"></div>{badge}</div>
    <h1>{headline}</h1>
    <p>{sub}</p>
  </div>
  <div class="bezel"><div class="screen"><img src="{img_uri}"></div></div>
</body></html>"""


def compose_frames(project: Path, log: Any = None, force: bool = False) -> dict[str, Any]:
    """Wrap each rendered scene in the marketing frame and write Play-ready PNGs."""
    spec = load_spec(project, create=False)
    scenes_dir = project / ".pipeline/storefront_scenes"
    if not scenes_dir.is_dir():
        return {"status": "ERROR", "error": "no rendered scenes — run storefront-scenes --render first"}

    manifest_file = scenes_dir / "manifest.json"
    if manifest_file.is_file() and json.loads(manifest_file.read_text()).get("provenance") != "compose_preview":
        return {"status": "ERROR", "error": "manifest is not compose_preview provenance"}

    chrome = _find_chrome_executable()
    if not chrome:
        return {"status": "ERROR", "error": "Google Chrome / Chromium not found"}

    from .marketing_asset_generator import _extract_brand_colors
    colors = _extract_brand_colors(project)
    module = project / (spec.get("module") or "")
    shipped = _app_accent(project, module if module.is_dir() else None)
    if shipped:
        colors["primary"] = shipped

    listing = project / "distribution/listing/en-US/images/phoneScreenshots"
    listing.mkdir(parents=True, exist_ok=True)
    frames_dir = project / ".pipeline/storefront_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    copy_by_id = {s["id"]: s for s in spec.get("scenes", [])}
    keep = {f"{s['id']}.png" for s in spec.get("scenes", [])}
    stale = _stale_screenshots(listing, keep)
    if log:
        for name in stale:
            log(f"  ! {name} is not owned by any scene — left in place; remove it if it is an old mock")
    written: list[dict[str, Any]] = []
    for png in sorted(scenes_dir.glob("*.png")):
        sid = png.stem
        scene = copy_by_id.get(sid, {"id": sid, "badge": "", "headline": "", "sub": ""})
        html_file = frames_dir / f"{png.stem}.html"
        out_png = listing / f"{sid}.png"
        if out_png.is_file() and not force:
            written.append({"id": sid, "file": str(out_png.relative_to(project)), "status": "SKIPPED (exists)"})
            continue
        html_file.write_text(_frame_html(scene, png.resolve().as_uri(), colors))
        try:
            subprocess.run(
                [chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
                 "--force-device-scale-factor=1", f"--window-size={FRAME_W},{FRAME_H}",
                 f"--screenshot={out_png.resolve()}", html_file.resolve().as_uri()],
                capture_output=True, text=True, timeout=60,
            )
        except Exception as e:
            written.append({"id": sid, "status": f"ERROR: {e}"})
            continue
        if out_png.is_file():
            dims = _png_dims(out_png)
            status = "OK"
            if dims != (FRAME_W, FRAME_H):
                status = f"WRONG_SIZE {dims} (expected {(FRAME_W, FRAME_H)}) — headless Chrome "                         "clamped the window; the frame is cut and must not ship"
            written.append({"id": sid, "file": str(out_png.relative_to(project)),
                            "bytes": out_png.stat().st_size, "status": status})
            if log:
                log(f"  ✓ frame {out_png.name} ({round(out_png.stat().st_size / 1024, 1)} KB)")
    return {"status": "OK", "frames": written,
            "count": len([w for w in written if w.get("status") == "OK"]),
            "stale": stale}


def _png_dims(path: Path) -> tuple[int, int] | None:
    try:
        head = path.read_bytes()[:24]
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            return struct.unpack(">II", head[16:24])
    except Exception:
        pass
    return None


def _app_accent(project: Path, module: Path | None = None) -> str | None:
    """Delegates to the shared implementation so frames and icons agree."""
    from .marketing_asset_generator import app_accent
    return app_accent(project)


def _stale_screenshots(listing: Path, keep: set[str]) -> list[str]:
    """Old screenshots that no scene owns — surfaced, never deleted (owner may have added them)."""
    if not listing.is_dir():
        return []
    return sorted(p.name for p in listing.glob("*.png") if p.name not in keep)


def preview_status(project: Path) -> dict[str, Any]:
    spec_file = project / SPEC_REL
    if not spec_file.is_file():
        return {"configured": False, "hint": f"run: devteam storefront-scenes <project> --scaffold"}
    spec = load_spec(project)
    manifest = project / ".pipeline/storefront_scenes/manifest.json"
    data = json.loads(manifest.read_text()) if manifest.is_file() else {}
    return {
        "configured": True,
        "narrative": spec.get("narrative"),
        "scenes": [s["id"] for s in spec.get("scenes", [])],
        "unbound": unbound_scenes(spec),
        "provenance": data.get("provenance"),
        "rendered": len(data.get("scenes", [])),
    }
