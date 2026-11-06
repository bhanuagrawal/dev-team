"""Design system generator and enforcement engine for dev-team.

Ensures that every project — greenfield (Mode B) or adapted existing (Mode A) —
has an authoritative, high-contrast, platform-modern design system specification
in `docs/design.md`, registered in `profile.json` `conventions`.

Zero project names in code. Gathers category standards via Tavily market search,
synthesizes a 2026-grade design law, and binds it into the pipeline's mandatory reads.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import adapt, tavily
from .core import RunLog, now_iso, pipeline_dir, profile_of, save_json


DESIGN_DOC_REL = "docs/design.md"

ARCHETYPES: dict[str, dict[str, Any]] = {
    "zen_focus": {
        "key": "zen_focus",
        "domain": "Digital Intentionality, Serenity & Focus",
        "keywords": ["launcher", "focus", "meditation", "calm", "serenity", "breathe", "breathing", "wellbeing", "detox", "habit", "habits", "routine", "streak", "intention", "minimalist phone"],
        "surface_base": "#0D1117",
        "surface_elevated": "#161C24",
        "surface_highlight": "#212B36",
        "primary_accent": "#10B981",       # Tranquil Emerald / Jade
        "secondary_accent": "#8B5CF6",     # Serene Amethyst
        "accent_success": "#10B981",
        "accent_warning": "#F59E0B",
        "accent_error": "#F43F5E",
        "text_primary": "#FFFFFF",
        "text_secondary": "#94A3B8",
        "text_tertiary": "#64748B",
        "headline_font": "Plus Jakarta Sans",
        "body_font": "Inter",
        "label_font": "JetBrains Mono",
        "aesthetic": "Tactile warm obsidian zen aesthetic with serene emerald accents, calm geometric breath indicators, and distraction-free micro-typography",
        "anti_patterns": "NEVER use high-stimulus fluorescent cyan neon glow, chaotic notification badges, or noisy promotional cards. Prioritize sensory tranquility, breath geometry, and 24dp+ spacious margins.",
    },
    "court_athletic": {
        "key": "court_athletic",
        "domain": "High-Adrenaline Sports & Competition",
        "keywords": ["scoreboard", "sports", "tennis", "fitness", "workout", "scorekeeper", "athletic", "match", "rally", "court"],
        "surface_base": "#05080F",
        "surface_elevated": "#121824",
        "surface_highlight": "#1D2638",
        "primary_accent": "#CCFF00",       # Electric Volt Lime
        "secondary_accent": "#FF3366",     # Flame Coral
        "accent_success": "#00E676",
        "accent_warning": "#FFB703",
        "accent_error": "#FF1744",
        "text_primary": "#FFFFFF",
        "text_secondary": "#94A3B8",
        "text_tertiary": "#64748B",
        "headline_font": "Anton",
        "body_font": "Barlow Condensed",
        "label_font": "JetBrains Mono",
        "aesthetic": "High-adrenaline stadium LED glanceability with massive score numerals, high outdoor sunlight contrast, and tactile court landmarks",
        "anti_patterns": "NEVER use pastel low-contrast tones or small touch targets. All numerals must be massive (72sp+), bold, and legible from 10 feet under direct sun.",
    },
    "technical_studio": {
        "key": "technical_studio",
        "domain": "Precision Technical Workstation & Tools",
        "keywords": ["pdf", "document", "scanner", "compress", "converter", "merge", "tools", "utility", "workstation", "file"],
        "surface_base": "#0B0F17",
        "surface_elevated": "#161E2E",
        "surface_highlight": "#222F47",
        "primary_accent": "#38BDF8",       # Precision Sky Cobalt
        "secondary_accent": "#F59E0B",     # Telemetry Amber
        "accent_success": "#10B981",
        "accent_warning": "#F59E0B",
        "accent_error": "#EF4444",
        "text_primary": "#FFFFFF",
        "text_secondary": "#94A3B8",
        "text_tertiary": "#64748B",
        "headline_font": "Space Grotesk",
        "body_font": "Inter",
        "label_font": "JetBrains Mono",
        "aesthetic": "High-precision studio workstation with interactive telemetry sliders, page canvas grids, and lossless data indicators",
        "anti_patterns": "NEVER use unstyled default cards or generic Material 3 containers. Provide concrete technical telemetry (e.g. 'Saved 16.3 MB • -88%').",
    },
    "financial_prestige": {
        "key": "financial_prestige",
        "domain": "Swiss Financial & Wealth Intelligence",
        "keywords": ["finance", "crypto", "wealth", "expense", "budget", "wallet", "investment", "accounting"],
        "surface_base": "#090D16",
        "surface_elevated": "#131B2E",
        "surface_highlight": "#1E293B",
        "primary_accent": "#F59E0B",       # Sovereign Gold
        "secondary_accent": "#3B82F6",     # Vault Sapphire
        "accent_success": "#10B981",
        "accent_warning": "#F59E0B",
        "accent_error": "#EF4444",
        "text_primary": "#FFFFFF",
        "text_secondary": "#94A3B8",
        "text_tertiary": "#64748B",
        "headline_font": "Sora",
        "body_font": "Inter",
        "label_font": "JetBrains Mono",
        "aesthetic": "Private wealth carbon prestige with micro-hairline borders and strictly tabular monetary numerals",
        "anti_patterns": "NEVER use playful cartoon illustrations or non-tabular numerals. Every monetary figure must use tnum formatting.",
    },
    "creative_media": {
        "key": "creative_media",
        "domain": "Immersive Cinematic Audio & Media",
        "keywords": ["music", "audio", "video", "player", "podcast", "camera", "photo", "gallery", "creative"],
        "surface_base": "#08080A",
        "surface_elevated": "#14141C",
        "surface_highlight": "#1E1F2C",
        "primary_accent": "#F43F5E",       # Neon Coral
        "secondary_accent": "#6366F1",     # Electric Indigo
        "accent_success": "#10B981",
        "accent_warning": "#F59E0B",
        "accent_error": "#F43F5E",
        "text_primary": "#FFFFFF",
        "text_secondary": "#A1A1AA",
        "text_tertiary": "#71717A",
        "headline_font": "Syne",
        "body_font": "Inter",
        "label_font": "JetBrains Mono",
        "aesthetic": "Deep void OLED canvas with radiant soundwave auras and edge-to-edge media cards",
        "anti_patterns": "NEVER use bright white backdrops or boxed outlines that break visual immersion.",
    },
    "editorial_literary": {
        "key": "editorial_literary",
        "domain": "Distraction-Free Literary & Knowledge",
        "keywords": ["reading", "books", "notes", "journal", "study", "education", "flashcards", "learn", "dictionary"],
        "surface_base": "#12100E",
        "surface_elevated": "#1C1917",
        "surface_highlight": "#292524",
        "primary_accent": "#D97706",       # Literary Amber
        "secondary_accent": "#059669",     # Sage Leaf
        "accent_success": "#10B981",
        "accent_warning": "#D97706",
        "accent_error": "#DC2626",
        "text_primary": "#FDF6E2",
        "text_secondary": "#A8A29E",
        "text_tertiary": "#78716C",
        "headline_font": "Newsreader",
        "body_font": "Literata",
        "label_font": "Space Mono",
        "aesthetic": "Tactile dark espresso warmth with literary serif typography and eye-strain-preventing amber accents",
        "anti_patterns": "NEVER use cold blue light accents or sharp geometric sans-serif for long-form reading surfaces.",
    },
}


def detect_domain_archetype(project: Path, topic: str = "") -> dict[str, Any]:
    """Classify the project's domain to assign an authentic, differentiated design system."""
    profile = profile_of(project)
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    name = profile.get("name") or project.name

    # Check for PRD or plan mentions
    prd_path = project / "docs/PRD.md"
    prd_snippet = prd_path.read_text(errors="ignore")[:500] if prd_path.is_file() else ""

    combined = f"{name} {vision} {topic} {prd_snippet}".lower()

    # The archetype decides a project's palette and typography, so it must not be
    # chosen by a monetisation sentence: a vision that says it will "earn money"
    # once classified a habit app as Swiss Financial. A profile may name its
    # design domain outright, and its declared market counts as evidence too.
    declared = str(profile.get("design_domain") or "").strip().lower()
    if declared in ARCHETYPES:
        return ARCHETYPES[declared]
    market = str(profile.get("market_term") or "").strip()
    if market:
        combined = f"{combined} {market.lower()}"

    for _k, arch in ARCHETYPES.items():
        if any(kw in combined for kw in arch["keywords"]):
            return arch

    # Fallback to modern high-contrast dark surface
    return {
        "key": "modern_dark",
        "domain": "Modern Elevated Dark Surface",
        "keywords": [],
        "surface_base": "#0A0E14",
        "surface_elevated": "#161F2E",
        "surface_highlight": "#202B3C",
        "primary_accent": "#00E5FF",
        "secondary_accent": "#7C4DFF",
        "accent_success": "#00E676",
        "accent_warning": "#FFB703",
        "accent_error": "#FF3D71",
        "text_primary": "#FFFFFF",
        "text_secondary": "#94A3B8",
        "text_tertiary": "#64748B",
        "headline_font": "Plus Jakarta Sans",
        "body_font": "Inter",
        "label_font": "JetBrains Mono",
        "aesthetic": f"Modern high-contrast dark-first {name} UI",
        "anti_patterns": "NEVER use default unstyled Android widgets. Maintain minimum 4.5:1 text contrast and minimum 48dp touch targets.",
    }


def _category_query(project: Path, topic: str = "") -> str:
    """Generate search query for Tavily market & UX research."""
    if topic.strip():
        return f"{project.name} {topic.strip()} modern mobile app UI UX design system color tokens typography 2026"
    profile = profile_of(project)
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    arch = detect_domain_archetype(project, topic)
    return f"{project.name} {arch['domain']} {vision} modern mobile app UI UX design system color tokens typography layout dark mode standards 2026"


def _build_design_template(project_name: str,
                           vision: str,
                           category_summary: str,
                           sources: list[str],
                           archetype: dict[str, Any] | None = None) -> str:
    """Synthesize an authoritative docs/design.md specification."""
    sources_md = "\n".join(f"- {s}" for s in sources[:4]) if sources else "- Official Google Android / Material 3 2026 Guidelines"
    arch = archetype or {
        "domain": "Modern Elevated Dark Surface",
        "surface_base": "#0A0E14",
        "surface_elevated": "#161F2E",
        "surface_highlight": "#202B3C",
        "primary_accent": "#00E5FF",
        "secondary_accent": "#7C4DFF",
        "accent_success": "#00E676",
        "accent_warning": "#FFB703",
        "accent_error": "#FF3D71",
        "text_primary": "#FFFFFF",
        "text_secondary": "#94A3B8",
        "text_tertiary": "#64748B",
        "headline_font": "Plus Jakarta Sans",
        "body_font": "Inter",
        "label_font": "JetBrains Mono",
        "aesthetic": f"Modern high-contrast dark-first {project_name} UI",
        "anti_patterns": "NEVER use default unstyled Android widgets. Maintain minimum 4.5:1 text contrast and minimum 48dp touch targets.",
    }

    return f"""# {project_name} Design System & Visual Specification (2026 Edition)

> **Authority:** This document is registered under `.pipeline/profile.json` `conventions`.  
> Per pipeline law, this is a **MANDATORY READ** for all developer and UI agents. Doctrine governs new files; for edited files, match and elevate to this standard.  
> **Domain Archetype:** `{arch['domain']}`

---

## 1. Product Philosophy & Visual Metaphor

**Core Thesis:** {vision or f"Elevate {project_name} to category-defining visual polish and usability."}

- **Visual Personality:** {arch['aesthetic']}
- **Aesthetic Tone:** High contrast, deliberate spatial hierarchy, polished surfaces, and fluid micro-interactions. No muddy greys, no low-contrast text, no unpadded tap targets.
- **Negative Directives & Anti-Patterns:** {arch['anti_patterns']}
- **Category Benchmark & Research Insights:**
{category_summary or "Modern 2026 mobile design emphasizes dark-first high-contrast palettes, tabular numerals for dynamic metrics, minimum 48dp touch targets, and full-bleed gesture-friendly surfaces."}

---

## 2. Color Palette & Token Architecture

The app adopts a **Dark-First, High-Contrast OLED** theme optimized for clarity, battery efficiency, and glanceability.

| Token | Hex | Role | Contrast / Usage |
|---|---|---|---|
| `SurfaceBase` | `{arch['surface_base']}` | True canvas base | Deep background canvas |
| `SurfaceElevated` | `{arch['surface_elevated']}` | Container surface (Level 1) | Primary content cards / tool panels |
| `SurfaceElevatedHigh` | `{arch['surface_highlight']}` | Highlighted surface (Level 2) | Active cards, dialogs, bottom sheets |
| `BrandPrimary` | `{arch['primary_accent']}` | Primary brand accent | Primary CTAs, active selections, focus rings |
| `BrandSecondary` | `{arch['secondary_accent']}` | Secondary brand accent | Badges, secondary toggles, progress fills |
| `AccentSuccess` | `{arch['accent_success']}` | Success State | Completed states, success confirmations |
| `AccentWarning` | `{arch['accent_warning']}` | Warning State | Destructive confirmations, alerts, limits |
| `AccentError` | `{arch['accent_error']}` | Critical Error | Critical errors, validation failures |
| `TextPrimary` | `{arch['text_primary']}` | Primary numerals & headings | Maximum contrast (WCAG AAA) |
| `TextSecondary` | `{arch['text_secondary']}` | Subtitles, labels, metadata | Minimum 4.5:1 contrast (WCAG AA) |
| `TextTertiary` | `{arch['text_tertiary']}` | Disabled indicators, dividers | Subtle borders and inactive outlines |

---

## 3. Typography Scale & Legibility

All typography must adhere to Jetpack Compose / Material 3 type tokens. Dynamic numerical metrics must always use **Tabular Numbers** (`FontFeatureSettings("tnum")`) to avoid layout jitter during value updates.

- Headline / Display Font: `{arch['headline_font']}`
- Body / Content Font: `{arch['body_font']}`
- Labels / Telemetry Font: `{arch['label_font']}`

```kotlin
object DesignTypography {{
    // Hero Numerals & Display Metrics
    val heroMetric = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 72.sp,
        fontWeight = FontWeight.Black,
        letterSpacing = (-1.5).sp
    )
    val titleLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 24.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = 0.sp
    )
    val titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 18.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 0.15.sp
    )
    val bodyLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 16.sp,
        fontWeight = FontWeight.Normal,
        lineHeight = 24.sp
    )
    val bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 14.sp,
        fontWeight = FontWeight.Normal,
        lineHeight = 20.sp
    )
    val labelSmall = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontSize = 12.sp,
        fontWeight = FontWeight.Medium,
        letterSpacing = 0.5.sp
    )
}}
```

---

## 4. Spacing, Radii & Layout Tokens

All layouts must strictly follow the **8-point spatial grid** (with 4dp used only for micro-spacing):

```kotlin
object SpacingTokens {{
    val xxs = 4.dp
    val xs = 8.dp
    val sm = 12.dp
    val md = 16.dp
    val lg = 24.dp
    val xl = 32.dp
    val xxl = 48.dp
}}

object RadiusTokens {{
    val sm = 8.dp
    val md = 16.dp
    val lg = 24.dp
    val pill = 999.dp
}}
```

### Layout Laws:
1. **Touch Target Size:** Every interactive touch target must be at least `48.dp x 48.dp` (`Modifier.sizeIn(minWidth = 48.dp, minHeight = 48.dp)`).
2. **Edge-to-Edge:** All screens must draw edge-to-edge behind system bars and use `WindowInsets.safeDrawing` or `safeContent` for inner paddings.
3. **Screen Padding:** Standard screen horizontal padding is `16.dp` on compact mobile displays, scaling gracefully on foldables/tablets.

---

## 5. Interaction, Motion & Micro-interactions

1. **Tactile Haptic Feedback:** Every primary state change (completion, tool execution, mode toggle) triggers immediate haptic confirmation (`HapticFeedbackType.LongPress` or `TextHandleMove`).
2. **Fluid Physics Animations:** State transitions must use spring physics (`spring(dampingRatio = Spring.DampingRatioMediumBouncy, stiffness = Spring.StiffnessLow)`) rather than abrupt linear cuts.
3. **Responsive Press States:** Cards and buttons must show subtle scale depression (`scale = 0.98f`) on pointer press.
4. **Honest Progress & States:**
   - Never show an infinite indeterminate spinner when a progress percentage or phase is known.
   - Every empty state must feature a relevant iconography, clear explanatory copy, and a direct primary action.

---

## 6. Anti-Patterns & Forbidden Practices

- ❌ **No Raw Hex Literals in Composables:** All colors must be sourced from the design tokens or theme object. Never write `Color(0xFF...)` inline in UI screens.
- ❌ **No Cramped Touch Targets:** Never place interactive icons or checkboxes in targets smaller than 48dp.
- ❌ **No Low-Contrast Text:** Secondary text must strictly pass WCAG AA standards (4.5:1 contrast against surface base).
- ❌ **No Unhandled Error Dead-Ends:** Errors must offer an inline retry or clear path back to the home state.
- ❌ **No Cookie-Cutter Generic Templates:** {arch['anti_patterns']}

---

## 7. Implementation Checklist for Developer Agent

When modifying or introducing any UI surface:
- [ ] Read this `docs/design.md` before writing Compose code.
- [ ] Verify background uses `SurfaceBase` and cards use `SurfaceElevated`.
- [ ] Confirm all touch targets have `Modifier.sizeIn(minWidth = 48.dp, minHeight = 48.dp)`.
- [ ] Ensure font sizes map to `DesignTypography` and dynamic numerals use tabular numbers.
- [ ] Verify haptics are wired to primary user actions.
- [ ] Run `python3 <team>/cli.py verify <project>` to validate before committing.

---

*Derived with research from:*
{sources_md}
"""


def ensure_design(project: Path,
                  topic: str = "",
                  force: bool = False,
                  log: RunLog | None = None) -> dict[str, Any]:
    """Ensure docs/design.md exists and is bound into profile conventions.

    If docs/design.md exists and force=False, ensures it is registered in conventions.
    If missing or force=True, researches category benchmarks via Tavily, synthesizes
    the design specification, writes docs/design.md, and updates profile conventions.
    """
    design_file = project / "docs" / "design.md"
    profile = profile_of(project)
    conventions = list(profile.get("conventions", []))
    resolved_design = str(design_file.resolve())

    already_registered = any(str(c).endswith("docs/design.md") or c == resolved_design for c in conventions)

    if design_file.exists() and not force:
        if not already_registered:
            if log:
                log(f"~ registering existing {DESIGN_DOC_REL} in conventions")
            conventions.append(resolved_design)
            profile["conventions"] = conventions
            save_json(pipeline_dir(project) / "profile.json", profile)
            adapt.run(project, log=log)
        return {
            "status": "exists",
            "path": str(design_file),
            "registered": True
        }

    # Synthesize design spec
    if log:
        log(f"~ synthesizing {DESIGN_DOC_REL} for {project.name}")

    query = _category_query(project, topic)
    market_report = tavily.search_market(project, query, log=log)
    
    answer = market_report.get("answer", "")
    sources = market_report.get("sources", [])
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    archetype = detect_domain_archetype(project, topic)

    content = _build_design_template(
        project_name=project.name,
        vision=vision,
        category_summary=answer,
        sources=sources,
        archetype=archetype,
    )

    design_file.parent.mkdir(parents=True, exist_ok=True)
    design_file.write_text(content)

    if log:
        log(f"  wrote {design_file.relative_to(project)} ({len(content)} chars)")

    # Register in conventions
    if not already_registered:
        conventions.append(resolved_design)
        profile["conventions"] = conventions
        save_json(pipeline_dir(project) / "profile.json", profile)

    # Adapt to sync state & verify
    adapt.run(project, log=log)

    return {
        "status": "created",
        "path": str(design_file),
        "query": query,
        "sources": len(sources),
        "registered": True
    }


def extract_design_tokens(project: Path) -> dict[str, str]:
    """Extract authoritative design tokens from docs/design.md.

    The token table is `| `BrandPrimary` | `#E8442A` | role |`, so the NAME cell is
    matched exactly. A substring test on the whole line is wrong and was observed
    wrong: `OnBrandPrimary` (the colour of text ON the accent) contains
    `BrandPrimary`, so it overwrote it — every generated poster, icon and Stitch
    prompt was then seeded with a near-black "accent".
    """
    arch = detect_domain_archetype(project)
    tokens = {
        "surface_base": arch["surface_base"],
        "surface_elevated": arch["surface_elevated"],
        "surface_highlight": arch["surface_highlight"],
        "primary_accent": arch["primary_accent"],
        "secondary_accent": arch["secondary_accent"],
        "warning_accent": arch["accent_warning"],
        "text_primary": arch["text_primary"],
        "text_secondary": arch["text_secondary"],
        "headline_font": arch["headline_font"],
        "body_font": arch["body_font"],
        "label_font": arch["label_font"],
    }
    design_doc = project / "docs/design.md"
    if not design_doc.is_file():
        return tokens
    import re
    text = design_doc.read_text(errors="ignore")

    roles = {
        "surfacebase": "surface_base",
        "surfaceelevated": "surface_elevated",
        "surfaceelevatedhigh": "surface_highlight",
        "brandprimary": "primary_accent",
        "servingaccent": "primary_accent",
        "brandsecondary": "secondary_accent",
        "receivingaccent": "secondary_accent",
        "accentwarning": "warning_accent",
        # Material-style tables name the same roles differently: a project whose
        # design.md says `background`/`accent` was seeded from the DEFAULTS
        # (#0A0E14 / #00E676) — another app's palette — so every generated icon
        # was designed against the wrong colours.
        "background": "surface_base",
        "surface": "surface_elevated",
        "surfacehigh": "surface_highlight",
        "surfacehighest": "surface_highlight",
        "accent": "primary_accent",
        "onsurface": "text_primary",
        "onsurfacevariant": "text_secondary",
    }
    matched = False
    for line in text.splitlines():
        m = re.match(r"\s*\|\s*`?([A-Za-z][\w -]*?)`?\s*\|\s*`?(#[0-9a-fA-F]{6})", line)
        if m:
            key = roles.get(m.group(1).replace(" ", "").lower())
            if key:
                tokens[key] = m.group(2)
                matched = True
    if matched:
        return tokens

    # Docs that are not a token table still carry colours on labelled lines.
    for line in text.splitlines():
        line = line.strip()
        m = re.search(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", line)
        hex_val = m.group(0)[:7] if m else None
        if not hex_val:
            continue
        if "SurfaceBase" in line or "background:" in line.lower():
            tokens["surface_base"] = hex_val
        elif "SurfaceElevatedHigh" in line or "surface-high:" in line.lower():
            tokens["surface_highlight"] = hex_val
        elif "SurfaceElevated" in line or "surface:" in line.lower():
            tokens["surface_elevated"] = hex_val
        elif "BrandPrimary" in line or "ServingAccent" in line or "accent:" in line.lower():
            tokens["primary_accent"] = hex_val
        elif "BrandSecondary" in line or "ReceivingAccent" in line or "accent-coral:" in line.lower():
            tokens["secondary_accent"] = hex_val
        elif "warning:" in line.lower() or "AccentWarning" in line:
            tokens["warning_accent"] = hex_val
    return tokens


def should_invoke_stitch(task_description: str, files_affected: list[str] | None = None) -> tuple[bool, str]:
    """Evaluate whether an incoming task requires Stitch MCP or should proceed in code/state.

    RATIONALE:
    - Invoke Stitch MCP when:
      * Creating an entirely new screen or user flow.
      * Major UI/UX redesign or spatial layout overhaul.
      * Defining brand assets (App Icon, Store Banner, Brand Identity).
      * Synthesizing high-contrast design system tokens for a greenfield app.
    - Bypass Stitch MCP when:
      * Bug fixes in business logic, rule engines, or state machines.
      * Data layer, local database, or network/API persistence.
      * Minor UI tweaks, margin/padding adjustments, copy changes.
      * Existing screen already matches docs/design.md tokens.
    """
    desc = task_description.lower()
    
    # Strong signals to invoke Stitch
    stitch_triggers = [
        "new screen", "create screen", "design screen", "ui redesign", "ux overhaul",
        "brand asset", "app icon", "feature graphic", "marketing mockup", "design system",
        "new layout", "visual language", "stitch"
    ]
    for trigger in stitch_triggers:
        if trigger in desc:
            return True, f"Triggered by '{trigger}': Task involves substantial visual or screen architecture design."

    # Strong signals to bypass Stitch
    code_only_triggers = [
        "bug", "fix", "crash", "state", "viewmodel", "logic", "unit test",
        "persistence", "database", "sql", "gradle", "ci", "workflow", "publish",
        "typo", "copy tweak", "padding", "refactor"
    ]
    for trigger in code_only_triggers:
        if trigger in desc and not any(t in desc for t in ["redesign", "new screen"]):
            return False, f"Bypassing Stitch: Task '{trigger}' is purely state, logic, or minor code maintenance."

    # Check files affected if provided
    if files_affected:
        has_new_screen_file = any("screen" in f.lower() or "ui" in f.lower() for f in files_affected)
        if has_new_screen_file:
            return True, "Affected files indicate a new or major UI screen surface."

    return False, "Default: Task does not explicitly require new visual architecture."


def curate_stitch_manifest(project: Path, screens_inventory: list[dict[str, Any]]) -> dict[str, Any]:
    """Maintain a curated, canonical manifest of Stitch screens to avoid junk accumulation.

    Rules:
    - Every screen instance on Stitch canvas must map to a canonical app surface.
    - Throwaway variants and outdated experiments must not clutter the project canvas.
    - Saves canonical manifest to .pipeline/stitch_manifest.json.
    """
    manifest_path = pipeline_dir(project) / "stitch_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    canonical_screens = []
    unmapped_or_stale = []

    # Screens explicitly matching the current release architecture / atmosphere
    # or core canonical assets without scratch/experimental modifiers
    scratch_tokens = ["shader", "loading", "idle", "edit mode", "widget management", "test", "scratch", "draft", "oled minimalist"]

    for s in screens_inventory:
        title = s.get("title", "")
        screen_id = s.get("name", "").split("/")[-1] if "/" in s.get("name", "") else s.get("id", "")
        t_low = title.lower()

        is_scratch = any(st in t_low for st in scratch_tokens)
        is_atmosphere_2 = "atmosphere 2.0" in t_low
        is_obsidian_studio = "obsidian" in t_low
        is_canonical_core = any(tok in t_low for tok in [
            "icon", "banner", "scoreboard", "summary", "setup", "design.md",
            "feature graphic", "storefront", "app drawer", "digital wellbeing",
            "screen time", "premium paywall", "pdf", "compress", "merge",
            "split", "convert", "rearrange", "workbench", "workstation",
            "document", "toolbox", "page studio"
        ]) and not is_scratch

        if (is_atmosphere_2 or is_obsidian_studio or is_canonical_core) and not is_scratch:
            canonical_screens.append({
                "id": screen_id,
                "title": title,
                "type": s.get("deviceType") or "CANONICAL",
                "status": "APPROVED",
            })
        else:
            unmapped_or_stale.append({
                "id": screen_id,
                "title": title,
                "status": "CANDIDATE_FOR_CLEANUP",
            })

    manifest = {
        "project": project.name,
        "updated_at": now_iso(),
        "canonical_screens": canonical_screens,
        "stale_screens": unmapped_or_stale,
        "screen_count": len(canonical_screens),
        "stale_count": len(unmapped_or_stale),
    }
    save_json(manifest_path, manifest)
    return manifest


def compile_stitch_screen_prompt(
    project: Path,
    screen_type: str = "main",
    spec: dict[str, Any] | None = None,
) -> str:
    """Compile an authoritative, anti-generic screen prompt for Stitch MCP.

    CORE PRINCIPLES:
    1. Pre-Locked Product Logic & CTAs: Every interactive element, button label,
       and state transition is defined upfront. Stitch must NOT improvise feature scope.
    2. Concrete Real-World Data: Zero dummy lorem ipsum. Exact scores, metrics,
       names, and telemetries are provided.
    3. UI/UX Realization Only: Layout craftsmanship, spatial hierarchy, typography
       contrast, radiant state auras, and tactile surfaces are delegated to Stitch.
    4. Anti-Generic Mandate: Explicit negative constraints ban standard Material 3 /
       Bootstrap cookie-cutter white cards, unstyled grey borders, and tiny buttons.
    """
    profile = profile_of(project)
    name = profile.get("name") or project.name
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    tokens = extract_design_tokens(project)

    st = screen_type.lower().strip()
    arch = detect_domain_archetype(project)
    arch_key = arch.get("key", "modern_dark")

    is_pdf = arch_key == "technical_studio" or "pdf" in name.lower() or "document" in vision.lower()
    is_court_sports = arch_key == "court_athletic" or "scoreboard" in vision.lower() or "tennis" in vision.lower()
    is_zen_focus = arch_key == "zen_focus" or any(k in vision.lower() for k in ["launcher", "focus", "meditation", "calm", "breathe", "detox"])

    if spec:
        screen_title = spec.get("title", f"{name} Screen")
        context_desc = spec.get("context", vision)
        layout_zones = spec.get("zones", [])
        ctas = spec.get("ctas", [])
        telemetry = spec.get("telemetry", {})
        aesthetic = spec.get("aesthetic", arch["aesthetic"])
    elif is_zen_focus:
        aesthetic = arch["aesthetic"]
        if st in ("main", "home", "hub", "core", "launcher"):
            screen_title = f"{name} - Intentional Home & Breathing Anchor"
            context_desc = f"Primary calm launch surface for {name}. Distraction-free, warm obsidian canvas with intentional breathing anchor and essential apps."
            layout_zones = [
                f"1. Top Quiet Header: Wordmark '{name.upper()}' in {tokens['headline_font']}, date in muted slate ({tokens['text_secondary']}), and subtle battery percentage.",
                f"2. Center Breathing Anchor Hero: Tactile geometric breath circle in tranquil emerald ({tokens['primary_accent']}) with soft ambient pulse aura, displaying '38m Intentional Use Left' and daily pickup count '12 Pickups'.",
                f"3. Essential Apps List: Clean distraction-free vertical list of user-prioritized apps with tabular usage limit badges (e.g. 'Messages', 'Phone', 'Calendar', 'Reading • 25m left').",
                f"4. Bottom Serenity Dock: Thumb-accessible pill buttons for 'Take a Breath Pause' (with {tokens['primary_accent']} accent glow) and 'All Apps Drawer'.",
            ]
            ctas = [
                "App Item (Launches app or triggers conscious pause friction)",
                "Breath Pause Button (Primary CTA -> Opens guided conscious breathing circle)",
                "Apps Drawer Pill (Opens full categorized app list)",
            ]
            telemetry = {
                "Today Screen Time": "42 mins (Down 64%)",
                "Intentional Pickups": "12",
                "Active Mode": "Intentional Detox",
            }
        elif st in ("pause", "breathe", "limits", "friction"):
            screen_title = f"{name} - Conscious Friction & Breath Pause"
            context_desc = f"Calm intentional pause overlay triggered before opening distracting apps, interrupting mindless dopamine loops."
            layout_zones = [
                f"1. Top Context Bar: Close 'X' button and subtle prompt 'CONSCIOUS PAUSE'.",
                f"2. Breathing Meditation Sphere: Concentric pulsating circle in serene emerald ({tokens['primary_accent']}) guiding the user to inhale and exhale.",
                f"3. Friction Countdown & Intentional Prompt: 'Take 2 deep breaths...' countdown timer in bold {tokens['headline_font']} numerals, followed by 'Opening Instagram'.",
                f"4. Conscious Choice Action Deck: High-contrast buttons:",
                f"   - Primary Action: 'OPEN WITH 15M LIMIT' in solid {tokens['primary_accent']} with dark text.",
                f"   - Secondary Action: 'STEP AWAY (CLOSE)' in elevated slate {tokens['surface_elevated']}.",
            ]
            ctas = [
                "Breathe & Wait (User observes the calming pulse rhythm)",
                "Open With Limit Button (Primary CTA -> Unlocks app for timed session)",
                "Step Away Button (Dismisses pause and returns to home screen)",
            ]
            telemetry = {
                "Target App": "Social App",
                "Pause Duration": "5 seconds remaining",
                "Session Limit": "15 minutes",
            }
        else:
            screen_title = f"{name} - Digital Wellbeing & Focus Limits"
            context_desc = f"App limits, bedtime monochrome schedule, and distraction detox settings for {name}."
            layout_zones = [
                f"1. Top Navigation Bar: Back button, title 'FOCUS & DETOX', reset defaults pill.",
                f"2. Usage Telemetry Card: Weekly phone time comparison chart in {tokens['surface_elevated']} with emerald {tokens['primary_accent']} progress bars.",
                f"3. App Friction Limit Rows: Modular cards with app title, toggle switch, and interactive session duration slider (5m, 15m, 30m).",
                f"4. Bedtime Grayscale Switch: Elevated card with toggle for automatic evening monochrome mode.",
                f"5. Save Action Button: Full-width button 'APPLY FOCUS SHIELD' in solid {tokens['primary_accent']}.",
            ]
            ctas = [
                "App Limit Slider (Adjusts allowed minutes per session)",
                "Grayscale Toggle (Enables night detox)",
                "Apply Focus Shield (Saves and locks configuration)",
            ]
            telemetry = {
                "Time Saved This Week": "4 hrs 18 mins",
                "Apps Restricted": "6 apps",
                "Monochrome Schedule": "10:30 PM - 7:00 AM",
            }
    elif is_pdf:
        aesthetic = "High-precision OLED dark-first PDF workstation studio UI with glowing neon accents"
        if st in ("main", "home", "hub", "core"):
            screen_title = f"{name} - Precision PDF Workstation (Home)"
            context_desc = f"Primary central workstation for {name}. High-contrast OLED dark mode with glowing accents, tactile cards, and zero bloat."
            layout_zones = [
                f"1. Top Context Header: Wordmark 'PDF KIT' in {tokens['headline_font']}, glowing trust pill '100% OFFLINE • PRIVATE' with green status dot, and settings gear button.",
                f"2. Primary Featured Hero Card: 'Smart Lossless Compress' featured card with electric cyan ({tokens['primary_accent']}) border glow, 'Reduce up to 88% • Zero Quality Loss' tag, and instant 'SELECT PDF' CTA button.",
                f"3. 2x2 Core Tools Grid: Elevated slate cards ({tokens['surface_elevated']}) with rounded corners and glowing glyphs:",
                f"   - Merge PDFs: Badge '2+ files', combines multiple documents into one.",
                f"   - Split PDF: Visual page range cutter icon with extract preview.",
                f"   - Convert: Dual toggle pill ('PDF ⇄ Images') with format selector.",
                f"   - Rearrange: Tactile drag-and-drop icon with page reorder overview.",
                f"4. Recent Documents Shelf: Clean technical document rows showing filename, page count ('24 pages'), file size ('3.8 MB'), timestamp ('Edited 2h ago'), and quick action dots.",
                f"5. Grounded Dock Bar: Fixed glassmorphic bottom bar with Tools, Compress, Recents, and Settings.",
            ]
            ctas = [
                "Select PDF Document (Primary hero CTA -> Launches file picker)",
                "Merge PDFs (Combines multiple documents into one)",
                "Split PDF (Visual page range cutter)",
                "Convert (Format selector)",
                "Rearrange (Tactile drag-and-drop page reorder)",
            ]
            telemetry = {
                "Engine": "Smart Lossless Compress",
                "Reduction": "-88%",
            }
        elif st in ("compress", "shrink", "optimize"):
            screen_title = f"{name} - Smart Lossless Compress"
            context_desc = f"On-device PDF compression studio with interactive reduction slider and live telemetry."
            layout_zones = [
                f"1. Top Navigation Bar: Back button, title 'Smart Compress', engine badge 'ENGINE V4.2 • LOSSLESS HYBRID', reset button.",
                f"2. File Inspection Card: Document thumbnail, title 'Annual_Report_2026.pdf', size '18.4 MB', 48 pages, vector data indicator.",
                f"3. Tactile Compression Slider: 3 segmented levels ('Print Hi-Fi ~40%', 'Recommended Balanced ~88%', 'Extreme ~95%') with active cyan ({tokens['primary_accent']}) slider rail.",
                f"4. Live Telemetry & Projected Output Card: Output projection '18.4 MB → 2.1 MB', glowing neon emerald savings badge '-88% (Saved 16.3 MB)'.",
                f"5. Primary Action CTA: 'COMPRESS PDF NOW' button with electric cyan glow and estimated time '~1.4s'.",
                f"6. Privacy & Security Footer: '100% On-Device Processing • Your file never leaves your phone'.",
            ]
            ctas = [
                "Back Button (Returns to Home)",
                "Compression Level Slider (Adjusts reduction target)",
                "Compress PDF Now (Primary CTA -> Executes lossless compression)",
            ]
            telemetry = {
                "Original Size": "18.4 MB (48 Pages)",
                "Projected Size": "2.1 MB (-88%)",
                "Engine": "Lossless Vector & Raster Downsampling",
            }
        elif st in ("merge", "rearrange", "canvas", "organize"):
            screen_title = f"{name} - Visual Page Canvas & Reorder"
            context_desc = f"Tactile multi-page document organizer, page rearranger, and combiner."
            layout_zones = [
                f"1. Top Navigation Bar: Back button, screen title 'PAGE STUDIO', page selection count '8 of 24 Selected'.",
                f"2. Canvas Action Bar: 'Rotate 90°', 'Select All', 'Invert Selection', 'Delete Pages'.",
                f"3. Visual Multi-Page Grid: 3-column thumbnail grid with drag handles, page numbers, and cyan ({tokens['primary_accent']}) selection borders.",
                f"4. Floating Action Deck: 'EXTRACT & MERGE SELECTED' button in solid {tokens['primary_accent']} with glow shadow.",
            ]
            ctas = [
                "Page Thumbnail (Toggles page selection)",
                "Rotate 90 Button (Rotates selected pages)",
                "Extract & Merge Button (Primary CTA -> Combines/exports selected pages)",
            ]
            telemetry = {
                "Document": "Portfolio_Draft_v3.pdf",
                "Selected Pages": "8 of 24",
                "Canvas Mode": "Visual Reorder & Extraction",
            }
        else:
            screen_title = f"{name} - Split & Convert Studio"
            context_desc = f"Extract specific page ranges or convert between PDF and high-res image sets."
            layout_zones = [
                f"1. Top Navigation Bar: Back button, title 'CONVERT & SPLIT'.",
                f"2. Conversion Direction Toggle: Segmented control for 'PDF → IMAGES' vs 'IMAGES → PDF'.",
                f"3. DPI & Quality Presets: High-contrast selector for 150 DPI (Web), 300 DPI (Print), and Custom.",
                f"4. Primary Action Button: 'CONVERT NOW' with high-contrast accent styling.",
            ]
            ctas = ["Direction Toggle", "DPI Selector", "Convert Now Button"]
            telemetry = {"Mode": "PDF to Images", "DPI": "300 DPI"}
    elif is_court_sports and st in ("score", "scoreboard", "main", "core"):
        aesthetic = "High-adrenaline dark-first sports-tech UI"
        screen_title = f"{name} - Active Court Scoreboard"
        context_desc = f"Primary active scoring surface for {name}. Engineered for rapid, sweaty-palm court-side glanceability under bright outdoor sunlight or indoor floodlights."
        layout_zones = [
            f"1. Top Context Header: Edge-to-edge status bar displaying match mode ('DOUBLES MATCH'), target rule ('PLAY TO 11 • WIN BY 2'), active court ('COURT #4'), and audio callout indicator.",
            f"2. Primary Interactive Arena (Split Court): Two massive full-bleed cards dividing the viewport vertically (Team 1 vs Team 2):",
            f"   - Serving Half (Top): Active glowing radiant aura border in {tokens['primary_accent']} with prominent server badge 'SERVER 2 (0-0-2)', team label 'TEAM 1 (Alex & Jordan)', and massive {tokens['headline_font']} score numeral '7'. Tap affordance label: 'TAP TO SCORE POINT'.",
            f"   - Receiving Half (Bottom): Deep slate surface {tokens['surface_elevated']} with crisp boundary stroke, team label 'TEAM 2 (Marcus & Sam)', and massive {tokens['headline_font']} score numeral '4'. Tap affordance label: 'TAP TO SCORE POINT'.",
            f"3. Center Court Landmark Divider: Non-Volley Zone (NVZ Kitchen) net divider line with geometric court cues and subtle net mesh accents separating the two halves.",
            f"4. Docked Court-Side Action Bar: Ergonomic thumb-accessible bottom utility deck with 3 distinct touch targets (min 48dp height):",
            f"   - 'Undo': Circular reverse arrow icon with label 'UNDO'.",
            f"   - 'Switch Server / Side-Out': High-contrast pill button styled with {tokens['primary_accent']} accent border.",
            f"   - 'Match Settings': Slate icon button with gear symbol.",
        ]
        ctas = [
            "Team 1 Scoring Zone (Full tap area -> +1 Point to Team 1)",
            "Team 2 Scoring Zone (Full tap area -> +1 Point to Team 2)",
            "Undo Button (Reverts last score update and restores previous serving state)",
            "Switch Server / Side-Out Button (Forces immediate server turnover or side-out)",
            "Settings Button (Opens match configuration bottom sheet)",
        ]
        telemetry = {
            "Team 1 Score": "7",
            "Team 2 Score": "4",
            "Active Server": "Server 2 (Doubles 0-0-2)",
            "Target Condition": "Play to 11, Win by 2",
            "Current Audio Call": "7 - 4 - 2",
        }
    elif is_court_sports and st in ("summary", "history", "stats", "recap"):
        aesthetic = "High-adrenaline dark-first sports-tech UI"
        screen_title = f"{name} - Post-Match Victory Summary & Performance Statistics"
        context_desc = f"Post-game victory celebration and detailed match telemetry breakdown for {name}."
        layout_zones = [
            f"1. Top Navigation Bar: Minimal back arrow, screen title 'MATCH RECAP', and court context badge 'COURT #4 • REC ELITE'.",
            f"2. Victory Hero Card: High-energy celebration card with display headline 'VICTORY — TEAM 1' (Alex Vance & Jordan Reed), illuminated neon emerald badge ({tokens['primary_accent']}) framing final score '11 - 9', and opponent subline 'Def. Team 2 (Marcus Stone & Sam Rivera)'.",
            f"3. 4-Card Telemetry Grid: 2x2 modular slate cards ({tokens['surface_elevated']}) with JetBrains Mono numbers:",
            f"   - Card A: '24:18' (Total Duration)",
            f"   - Card B: '4' (Side-Outs)",
            f"   - Card C: '18 Shots' (Longest Rally)",
            f"   - Card D: '68%' (1st Server Conversion Efficiency)",
            f"4. Comparative Performance Breakdown: Head-to-head progress bars comparing Team 1 vs Team 2 for side-out conversion, unforced kitchen faults, and momentum streaks.",
            f"5. Docked Action Deck: Fixed bottom button group:",
            f"   - Primary High-Impact CTA: 'START NEXT GAME' in solid vibrant {tokens['primary_accent']} with dark bold text.",
            f"   - Secondary Action CTA: 'SHARE MATCH RECAP' in outlined slate with share glyph.",
        ]
        ctas = [
            "Start Next Game (Primary CTA -> Launches new match setup)",
            "Share Match Recap (Secondary CTA -> Exports formatted scorecard)",
            "Back to Home (Header action -> Returns to session dashboard)",
        ]
        telemetry = {
            "Final Score": "11 - 9",
            "Winner": "Team 1 (Alex Vance & Jordan Reed)",
            "Duration": "24 mins 18 secs",
            "Side-Outs": "4",
            "Serve Efficiency": "Team 1: 68% vs Team 2: 48%",
        }
    elif is_court_sports and st in ("setup", "config", "new"):
        aesthetic = "High-adrenaline dark-first sports-tech UI"
        screen_title = f"{name} - New Match Configuration"
        context_desc = f"Pre-match ruleset and player configuration screen for {name}."
        layout_zones = [
            f"1. Top Navigation Bar: Close button, screen title 'NEW MATCH SETUP', and reset to default pill.",
            f"2. Match Format Segmented Card: High-contrast selector with chunky hit targets:",
            f"   - Option 1 (Selected): 'DOUBLES (2v2)' highlighted with vibrant {tokens['primary_accent']} fill.",
            f"   - Option 2: 'SINGLES (1v1)' in neutral slate.",
            f"3. Points To Win Card: Segmented 3-option pill row:",
            f"   - '[ 11 PTS ]' (Active default) • '15 PTS' • '21 PTS'.",
            f"4. Overtime Rule Card: Dual option cards for 'WIN BY 2' (Traditional USAPA rule) vs 'SUDDEN DEATH (POINT CAP)'.",
            f"5. Audio & Voice Announce Toggle: Elevated slate card with toggle switch for automatic synthesized score callouts.",
            f"6. Primary Launch Action: Full-width high-impact CTA button 'START MATCH' in solid {tokens['primary_accent']} with luminous glow shadow.",
        ]
        ctas = [
            "Format Toggle (Switches between Singles and Doubles rules)",
            "Score Target Pills (Selects 11, 15, or 21 points)",
            "Overtime Rule Switch (Toggles Win by 2 vs Sudden Death)",
            "Audio Announce Toggle (Enables/disables speech synthesis)",
            "Start Match Button (Primary CTA -> Transitions to scoreboard)",
        ]
        telemetry = {
            "Selected Format": "Doubles (2v2)",
            "Target Points": "11 Points",
            "Overtime Mode": "Win by 2",
            "Audio Scorekeeper": "Enabled",
        }
    else:
        aesthetic = f"Modern high-contrast dark-first {name} UI"
        screen_title = f"{name} - {screen_type.capitalize()} Screen"
        context_desc = f"User interface surface for {name}: {vision}"
        layout_zones = [
            f"1. Top Context Bar: Clear screen title and navigation.",
            f"2. Primary Content Section: Structured modular cards using {tokens['surface_elevated']}.",
            f"3. Action Controls: High-contrast thumb-accessible buttons using {tokens['primary_accent']}.",
        ]
        ctas = ["Primary Action Button", "Secondary Action Option"]
        telemetry = {"Status": "Active"}

    zones_text = "\n".join(layout_zones)
    ctas_text = "\n".join(f"- {c}" for c in ctas)
    telemetry_text = "\n".join(f"- {k}: {v}" for k, v in telemetry.items())

    return f"""Title: {screen_title}
Device: Mobile (390px viewport width, portrait)
Platform Aesthetic: {aesthetic}

=== SECTION 1: PRODUCT INTENT & CONTEXT ===
{context_desc}

=== SECTION 2: PRE-LOCKED INTERACTION ARCHITECTURE & EXACT CTAs ===
All functional CTAs and interaction states are locked upfront. Realize this exact flow without omitting or inventing features:
{ctas_text}

=== SECTION 3: EXACT DATA TELEMETRY (NO LOREM IPSUM) ===
Render these exact real-world values into the layout:
{telemetry_text}

=== SECTION 4: SCREEN LAYOUT & SPATIAL ZONES ===
{zones_text}

=== SECTION 5: STRICT ANTI-GENERIC VISUAL CRAFT MANDATE ===
- Domain Archetype: {arch['domain']}
- Canvas Base: True background ({tokens['surface_base']}) with subtle atmospheric lighting.
- Card Containers: Elevated dark surface ({tokens['surface_elevated']}) with crisp structural borders ({tokens.get('surface_highlight', '#202B3C')}).
- Active Radiant Aura: The currently active action state MUST feature a glowing radiant aura ({tokens['primary_accent']}) so users recognize interactive focus instantly.
- Secondary Accent: Use secondary accent ({tokens['secondary_accent']}) for badges, progress fills, and status transitions.
- Warning / Metric Accent: Use warm accent ({tokens['warning_accent']}) for alert confirmations, limits, and telemetry.
- Typography Scale:
  * Numbers & Metric Heroes: Bold {tokens['headline_font']} numerals with tabular spacing.
  * Headings & Section Titles: Modern technical {tokens['body_font']}.
  * Status Badges & Pills: Monospaced {tokens['label_font']} with uppercase letter-spacing.
- ANTI-PATTERNS (STRICTLY FORBIDDEN):
  * {arch['anti_patterns']}
  * NO generic white card templates or muddy grey backgrounds.
  * NO low-contrast grey-on-dark text (all secondary text must maintain 4.5:1 WCAG AA contrast).
  * NO cramped interactive touch targets (every tap zone must be min 48dp x 48dp).
  * NO generic floating action buttons (FABs) obscuring content.
"""

