"""Design system generator and enforcement engine for dev-team.

Ensures that every project — greenfield (Mode B) or adapted existing (Mode A) —
has an authoritative, high-contrast, platform-modern design system specification
in `docs/design.md`, registered in `profile.json` `conventions`.

Zero project names in code. Gathers category standards via Tavily market search,
synthesizes a 2026-grade design law, and binds it into the pipeline's mandatory reads.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import adapt, tavily
from .core import RunLog, now_iso, pipeline_dir, profile_of, save_json


DESIGN_DOC_REL = "docs/design.md"

ARCHETYPES: dict[str, dict[str, Any]] = {
    "zen_focus": {
        "key": "zen_focus",
        "domain": "Digital Intentionality, Serenity & Focus",
        "theme_mode": "warm",
        "keywords": ["launcher", "focus", "meditation", "calm", "serenity", "breathe", "breathing", "wellbeing", "detox", "habit", "habits", "routine", "streak", "intention", "minimalist phone"],
        "surface_base": "#F7F5F0",       # Soft warm stone alabaster
        "surface_elevated": "#FFFFFF",   # Crisp white card surface
        "surface_highlight": "#EDE8DF",  # Warm stone highlight
        "primary_accent": "#2D5A46",     # Deep Forest Sage
        "secondary_accent": "#B86B3E",   # Warm Terracotta
        "accent_success": "#2D5A46",
        "accent_warning": "#D97706",
        "accent_error": "#DC2626",
        "text_primary": "#1C201D",       # Deep charcoal pine
        "text_secondary": "#68706A",     # Muted sage slate
        "text_tertiary": "#9BA39D",
        "headline_font": "Plus Jakarta Sans",
        "body_font": "Inter",
        "label_font": "JetBrains Mono",
        "aesthetic": "Sensory tranquility and organic mindfulness with warm stone surfaces, earthy forest sage accents, spacious 24dp+ padding, and distraction-free breathing typography",
        "anti_patterns": "NEVER use dark cyberpunk OLED themes, fluorescent cyan laser glow, or badge spam. Prioritize calm daylight surfaces, natural stone contrast, and spacious margins.",
    },
    "editorial_paper": {
        "key": "editorial_paper",
        "domain": "Distraction-Free Literary, Notes & Knowledge",
        "theme_mode": "warm",
        "keywords": ["books", "book", "journal", "reading", "notes", "flashcards", "study", "education", "dictionary", "library", "article", "literature"],
        "surface_base": "#FAF7F2",       # Warm linen paper
        "surface_elevated": "#FFFFFF",   # Pure white card surface
        "surface_highlight": "#F3ECE2",  # Warm sand accent
        "primary_accent": "#9E472A",     # Burnt Terracotta
        "secondary_accent": "#2B4C3F",   # Deep Pine
        "accent_success": "#2B4C3F",
        "accent_warning": "#D97706",
        "accent_error": "#DC2626",
        "text_primary": "#1C1917",       # Deep Espresso Ink
        "text_secondary": "#78716C",     # Warm Stone
        "text_tertiary": "#A8A29E",
        "headline_font": "Newsreader",
        "body_font": "Literata",
        "label_font": "Space Mono",
        "aesthetic": "Tactile editorial paper aesthetic with rich espresso ink typography, literary serif headings, generous breathing whitespace, and organic bookcraft materiality",
        "anti_patterns": "NEVER use cold dark OLED backgrounds, neon glow, or sharp robotic boxes. Prioritize warm paper tones and literary elegance.",
    },
    "clean_utility": {
        "key": "clean_utility",
        "domain": "Precision Tools, Utility & File Workstations",
        "theme_mode": "light",
        "keywords": ["pdf", "scanner", "compress", "converter", "merge pdf", "workstation", "calculator", "file management", "file converter", "utility tools"],
        "surface_base": "#F8FAFC",       # Slate 50 daylight canvas
        "surface_elevated": "#FFFFFF",   # Pure white card surface (1px #E2E8F0 border)
        "surface_highlight": "#F1F5F9",  # Slate 100 highlight
        "primary_accent": "#2563EB",     # Cobalt Royal
        "secondary_accent": "#059669",   # Precision Emerald
        "accent_success": "#059669",
        "accent_warning": "#D97706",
        "accent_error": "#DC2626",
        "text_primary": "#0F172A",       # Slate 900
        "text_secondary": "#64748B",     # Slate 500
        "text_tertiary": "#94A3B8",      # Slate 400
        "headline_font": "Space Grotesk",
        "body_font": "Inter",
        "label_font": "JetBrains Mono",
        "aesthetic": "Crisp daylight clarity with human-crafted micro-borders, airy whitespace, purposeful cobalt telemetry, and functional elevation",
        "anti_patterns": "NEVER use muddy grey backgrounds, neon laser glow, or meaningless badge spam. Keep utility surfaces bright, legible, and frictionless.",
    },
    "court_athletic": {
        "key": "court_athletic",
        "domain": "High-Adrenaline Sports & Competition",
        "theme_mode": "dark",
        "keywords": ["scoreboard", "sports", "tennis", "pickleball", "racket", "racquet", "match", "rally", "court", "scorekeeper", "athletic", "referee"],
        "surface_base": "#0A0D14",       # Deep Stadium Shadow
        "surface_elevated": "#141A24",   # Court Slate
        "surface_highlight": "#1F2736",
        "primary_accent": "#CCFF00",     # Electric Volt Lime
        "secondary_accent": "#FF5722",   # High-Vis Court Blaze
        "accent_success": "#00E676",
        "accent_warning": "#FFB703",
        "accent_error": "#FF1744",
        "text_primary": "#FFFFFF",
        "text_secondary": "#94A3B8",
        "text_tertiary": "#64748B",
        "headline_font": "Anton",
        "body_font": "Barlow Condensed",
        "label_font": "JetBrains Mono",
        "aesthetic": "High-adrenaline stadium glanceability with massive scoreboard numerals, stark court landmarks, and rapid single-tap action zones",
        "anti_patterns": "NEVER use pastel low-contrast tones or small touch targets. All numerals must be massive (72sp+), bold, and legible from 10 feet under direct sun.",
    },
    "financial_prestige": {
        "key": "financial_prestige",
        "domain": "Swiss Financial & Wealth Intelligence",
        "theme_mode": "light",
        "keywords": ["finance", "financial", "crypto", "wealth", "expense", "expenses", "budget", "budgeting", "wallet", "investment", "accounting"],
        "surface_base": "#F9FAFB",       # Clean Off-White
        "surface_elevated": "#FFFFFF",
        "surface_highlight": "#F3F4F6",
        "primary_accent": "#0F52BA",     # Sapphire Vault
        "secondary_accent": "#D97706",   # Sovereign Amber
        "accent_success": "#10B981",
        "accent_warning": "#F59E0B",
        "accent_error": "#EF4444",
        "text_primary": "#111827",
        "text_secondary": "#6B7280",
        "text_tertiary": "#9CA3AF",
        "headline_font": "Sora",
        "body_font": "Inter",
        "label_font": "JetBrains Mono",
        "aesthetic": "Swiss international typographic style with micro-hairline borders, strict tabular monetary alignment, and understated luxury",
        "anti_patterns": "NEVER use playful cartoon illustrations, neon laser glows, or non-tabular numerals. Every monetary figure must use tnum formatting.",
    },
    "creative_media": {
        "key": "creative_media",
        "domain": "Immersive Cinematic Audio & Media",
        "theme_mode": "dark",
        "keywords": ["music player", "video player", "audio player", "podcast", "camera", "photo gallery", "media streaming", "creative studio", "video editor"],
        "surface_base": "#0C0D12",       # Velvet Charcoal Obsidian
        "surface_elevated": "#181A22",
        "surface_highlight": "#242734",
        "primary_accent": "#F43F5E",     # Rose Coral
        "secondary_accent": "#6366F1",   # Electric Indigo
        "accent_success": "#10B981",
        "accent_warning": "#F59E0B",
        "accent_error": "#F43F5E",
        "text_primary": "#FFFFFF",
        "text_secondary": "#A1A1AA",
        "text_tertiary": "#71717A",
        "headline_font": "Syne",
        "body_font": "Inter",
        "label_font": "JetBrains Mono",
        "aesthetic": "Immersive velvet obsidian canvas with edge-to-edge media bleeding, tonal depth, and fluid gestures",
        "anti_patterns": "NEVER use bright white backdrops or boxed outlines that break visual immersion.",
    },
    "playful_consumer": {
        "key": "playful_consumer",
        "domain": "Consumer Habit, Social & Gamified",
        "theme_mode": "warm",
        "keywords": ["gamified", "gamification", "kids", "quiz", "trivia", "social community"],
        "surface_base": "#FAF8F5",
        "surface_elevated": "#FFFFFF",
        "surface_highlight": "#F3EFEA",
        "primary_accent": "#FF6B4A",     # Warm Coral
        "secondary_accent": "#5B5BD6",   # Cobalt Violet
        "accent_success": "#10B981",
        "accent_warning": "#F59E0B",
        "accent_error": "#EF4444",
        "text_primary": "#18181B",
        "text_secondary": "#71717A",
        "text_tertiary": "#A1A1AA",
        "headline_font": "Plus Jakarta Sans",
        "body_font": "Inter",
        "label_font": "JetBrains Mono",
        "aesthetic": "Playful tactile consumer warmth with pill buttons, rich colored accents, organic cards, and delightful micro-interactions",
        "anti_patterns": "NEVER use cold dark cyber styling, generic SaaS grids, or intimidating technical jargon.",
    },
    "technical_studio": {
        "key": "technical_studio",
        "domain": "Precision Technical Workstation & Studio",
        "theme_mode": "dark",
        "keywords": ["developer", "terminal", "debugger", "git", "code", "ide", "network"],
        "surface_base": "#0B0F17",
        "surface_elevated": "#161E2E",
        "surface_highlight": "#222F47",
        "primary_accent": "#38BDF8",     # Precision Sky Cobalt
        "secondary_accent": "#F59E0B",   # Telemetry Amber
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
}


def detect_domain_archetype(project: Path, topic: str = "") -> dict[str, Any]:
    """Classify the project's domain to assign an authentic, differentiated design system."""
    profile = profile_of(project)
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    name = profile.get("name") or project.name

    # The archetype decides a project's palette and typography. A profile may name its
    # design domain or theme preference outright.
    declared = str(profile.get("design_domain") or "").strip().lower()
    if declared in ARCHETYPES:
        return ARCHETYPES[declared]

    market = str(profile.get("market_term") or "").strip()
    prd_path = project / "docs/PRD.md"
    prd_snippet = prd_path.read_text(errors="ignore")[:600] if prd_path.is_file() else ""
    prd_clean = re.sub(r"this document is\w*|markdown|authority|conventions", "", prd_snippet.lower())

    name_market = f"{name} {market}".lower()
    vision_lower = vision.lower()
    topic_lower = topic.lower()

    scores = {k: 0 for k in ARCHETYPES}
    for k, arch in ARCHETYPES.items():
        score = 0
        for kw in arch["keywords"]:
            pattern = r"\b" + re.escape(kw) + r"\b"
            if re.search(pattern, name_market):
                score += 5
            if re.search(pattern, vision_lower):
                score += 3
            if topic_lower and re.search(pattern, topic_lower):
                score += 3
            if prd_clean and re.search(pattern, prd_clean):
                score += 1
        scores[k] = score

    best_key = max(scores, key=scores.get)
    if scores[best_key] > 0:
        return ARCHETYPES[best_key]

    # Fallback to daylight clean utility — NOT generic dark cyan AI template
    return ARCHETYPES["clean_utility"]


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
    arch = archetype or ARCHETYPES["clean_utility"]
    theme_mode = arch.get("theme_mode", "light")
    if theme_mode in ("light", "warm"):
        theme_desc = f"The app adopts a **Human-Crafted, Daylight / Warm {arch['domain']}** theme optimized for clarity, natural materiality, and sustained reading comfort."
    else:
        theme_desc = f"The app adopts an **Atmospheric High-Contrast Dark** theme optimized for glanceability, focus, and battery efficiency."

    return f"""# {project_name} Design System & Visual Specification (2026 Edition)

> **Authority:** This document is registered under `.pipeline/profile.json` `conventions`.  
> Per pipeline law, this is a **MANDATORY READ** for all developer and UI agents. Doctrine governs new files; for edited files, match and elevate to this standard.  
> **Domain Archetype:** `{arch['domain']}` (Mode: `{theme_mode.upper()}`)

---

## 1. Product Philosophy & Visual Metaphor

**Core Thesis:** {vision or f"Elevate {project_name} to category-defining visual polish and usability."}

- **Visual Personality:** {arch['aesthetic']}
- **Aesthetic Tone:** High contrast, deliberate spatial hierarchy, polished surfaces, and fluid micro-interactions. No muddy greys, no low-contrast text, no unpadded tap targets.
- **Negative Directives & Anti-Patterns:** {arch['anti_patterns']}
- **Category Benchmark & Research Insights:**
{category_summary or "Modern 2026 mobile design emphasizes human-crafted palettes, purposeful typography contrast, tabular numerals for dynamic metrics, minimum 48dp touch targets, and full-bleed gesture-friendly surfaces."}

---

## 2. Color Palette & Token Architecture

{theme_desc}

| Token | Hex | Role | Contrast / Usage |
|---|---|---|---|
| `SurfaceBase` | `{arch['surface_base']}` | True canvas base | Base background canvas |
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

- ❌ **No AI Cliché Styling:** NEVER use artificial glowing neon halos, laser borders, or sci-fi radiant auras. Use authentic elevation via subtle 1px micro-borders and soft diffuse shadows.
- ❌ **No Uniform Card Grids:** Never generate a wall of identical stacked cards with identical padding and borders. Use varied layout hierarchy with distinct visual anchors.
- ❌ **No Pseudo-Tech Badge Spam:** Never clutter headers and action zones with fake status pills ("ENGINE V4.2 PRO", "AI OPTIMIZED", etc.).
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
    """RETIRED (owner directive 2026-11-09): docs/design.md is not a design source.

    The Stitch project and its exported screens under docs/stitch/screens/<id>/
    are the only visual authority.
    """
    return {"status": "retired", "path": "",
            "note": "design.md is not a design source; use the Stitch screens"}


def _hex_luminance(hex_color: str) -> float | None:
    h = (hex_color or "").strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        r, g, b = int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0
    except ValueError:
        return None
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _tokens_from_stitch_html(project: Path) -> dict[str, str]:
    """Extract real palette & typography tokens from exported Stitch HTML."""
    screens_dir = project / "docs" / "stitch" / "screens"
    if not screens_dir.is_dir():
        return {}
    html_files = sorted(screens_dir.glob("*/index.html"))
    if not html_files:
        return {}

    extracted: dict[str, str] = {}
    for hp in html_files:
        text = hp.read_text(errors="ignore")
        # Parse tailwind-config colors/fontFamily when present
        colors_m = re.search(r'"colors"\s*:\s*\{([^}]+)\}', text, re.S)
        if colors_m:
            block = colors_m.group(1)
            cmap = dict(re.findall(r'"([a-z0-9\-]+)"\s*:\s*"(#[0-9a-fA-F]{6})"', block, re.I))
            mapping = {
                "background": "surface_base",
                "surface": "surface_base",
                "surface-container": "surface_elevated",
                "surface-container-low": "surface_elevated",
                "surface-container-high": "surface_highlight",
                "surface-container-highest": "surface_highlight",
                "primary": "primary_accent",
                "secondary": "secondary_accent",
                "tertiary": "warning_accent",
                "on-surface": "text_primary",
                "on-background": "text_primary",
                "on-surface-variant": "text_secondary",
            }
            for src_k, dst_k in mapping.items():
                if src_k in cmap and dst_k not in extracted:
                    extracted[dst_k] = cmap[src_k]
        fonts_m = re.search(r'"fontFamily"\s*:\s*\{([^}]+)\}', text, re.S)
        if fonts_m:
            fblock = fonts_m.group(1)
            fmap = re.findall(r'"([a-z0-9\-]+)"\s*:\s*\[\s*"([^"]+)"', fblock, re.I)
            for fk, fv in fmap:
                fl = fk.lower()
                if ("headline" in fl or "display" in fl) and "headline_font" not in extracted:
                    extracted["headline_font"] = fv
                elif ("body" in fl or "title" in fl) and "body_font" not in extracted:
                    extracted["body_font"] = fv
                elif "label" in fl and "label_font" not in extracted:
                    extracted["label_font"] = fv
        if extracted:
            break
    return extracted


def extract_design_tokens(project: Path) -> dict[str, str]:
    """Extract authoritative design tokens from Stitch artifacts, then legacy fallback.

    Priority:
    1. `.pipeline/stitch_tokens.json` or `docs/stitch/tokens.json`
    2. Exported Stitch HTML (`docs/stitch/screens/*/index.html`)
    3. Legacy `docs/design.md` (if present)
    4. Domain archetype fallback
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
        "theme_mode": arch.get("theme_mode", "light"),
        "source": "archetype",
    }

    # 1. Explicit Stitch token JSON
    for tok_path in (pipeline_dir(project) / "stitch_tokens.json",
                     project / "docs" / "stitch" / "tokens.json"):
        if tok_path.is_file():
            try:
                raw = json.loads(tok_path.read_text())
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if k in tokens and isinstance(v, str) and v.strip():
                            tokens[k] = v.strip()
                    tokens["source"] = "stitch_tokens"
                    lum = _hex_luminance(tokens["surface_base"])
                    if lum is not None:
                        tokens["theme_mode"] = "dark" if lum < 0.35 else "light"
                    return tokens
            except Exception:
                pass

    # 2. Exported Stitch screen HTML
    html_tokens = _tokens_from_stitch_html(project)
    if html_tokens:
        tokens.update(html_tokens)
        tokens["source"] = "stitch_html"
        lum = _hex_luminance(tokens["surface_base"])
        if lum is not None:
            tokens["theme_mode"] = "dark" if lum < 0.35 else "light"
        return tokens

    # 3. Legacy docs/design.md fallback (older projects only)
    design_doc = project / "docs/design.md"
    if not design_doc.is_file():
        return tokens
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
        tokens["source"] = "design_doc"
        lum = _hex_luminance(tokens["surface_base"])
        if lum is not None:
            tokens["theme_mode"] = "dark" if lum < 0.35 else "light"
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
            tokens["source"] = "design_doc"
        elif "SurfaceElevatedHigh" in line or "surface-high:" in line.lower():
            tokens["surface_highlight"] = hex_val
        elif "SurfaceElevated" in line or "surface:" in line.lower():
            tokens["surface_elevated"] = hex_val
        elif "BrandPrimary" in line or "ServingAccent" in line or "accent:" in line.lower():
            tokens["primary_accent"] = hex_val
            tokens["source"] = "design_doc"
        elif "BrandSecondary" in line or "ReceivingAccent" in line or "accent-coral:" in line.lower():
            tokens["secondary_accent"] = hex_val
        elif "warning:" in line.lower() or "AccentWarning" in line:
            tokens["warning_accent"] = hex_val
    lum = _hex_luminance(tokens["surface_base"])
    if lum is not None:
        tokens["theme_mode"] = "dark" if lum < 0.35 else "light"
    return tokens


def recommend_stitch_mode(
    task_description: str,
    has_existing_screen: bool = False,
    files_affected: list[str] | None = None,
) -> dict[str, str]:
    """Choose the least destructive Stitch action: bypass | tweak | variants | new_screen.

    Prefers targeted tweaks (`edit_screens` / `generate_variants` REFINE) over
    complete redesign (`generate_screen_from_text`) whenever an existing screen
    already covers the surface.
    """
    desc = (task_description or "").lower()

    code_only_triggers = [
        "bug", "crash", "viewmodel", "logic", "unit test",
        "persistence", "database", "sql", "gradle", "ci", "workflow", "publish",
        "typo", "refactor",
    ]
    new_or_redesign_triggers = [
        "new screen", "create screen", "from scratch", "ground-up",
        "complete redesign", "full redesign", "ux overhaul", "revamp",
        "app icon", "feature graphic", "brand asset",
    ]
    tweak_triggers = [
        "tweak", "adjust", "refine", "polish", "spacing", "padding",
        "contrast", "alignment", "button", "copy", "label", "badge",
        "empty state", "dialog", "sheet", "chip", "card", "gripe",
        "update screen", "visual", "layout", "color", "font", "hierarchy",
    ]
    variant_triggers = ["variant", "explore", "alternative", "options"]

    if any(t in desc for t in new_or_redesign_triggers) or re.search(r"\b(?:new|add|build|create)\b.{0,24}\bscreen\b", desc):
        # Even for "ui redesign" on an existing screen, warn toward tweak unless truly new/revamp.
        is_explicit_new = (
            any(t in desc for t in ("new screen", "create screen", "from scratch", "ground-up", "revamp", "app icon", "feature graphic"))
            or bool(re.search(r"\b(?:new|create)\b.{0,24}\bscreen\b", desc))
        )
        if has_existing_screen and not is_explicit_new:
            return {
                "mode": "tweak",
                "reason": "Existing screen covers this surface — apply targeted edit_screens tweak instead of complete redesign.",
            }
        return {
            "mode": "new_screen",
            "reason": "Task explicitly requires a new screen, brand asset, or ground-up revamp.",
        }

    if has_existing_screen and any(t in desc for t in variant_triggers):
        return {
            "mode": "variants",
            "reason": "Existing screen present and alternatives requested — use generate_variants (REFINE).",
        }

    if any(t in desc for t in tweak_triggers):
        return {
            "mode": "tweak" if has_existing_screen else "new_screen",
            "reason": (
                "Visual/layout adjustment on an existing surface — use Stitch edit_screens to tweak in place."
                if has_existing_screen
                else "Visual task with no existing Stitch screen — generate initial screen."
            ),
        }

    for trigger in code_only_triggers:
        if trigger in desc:
            return {
                "mode": "bypass",
                "reason": f"Bypassing Stitch: '{trigger}' is logic, state, or code maintenance.",
            }

    if files_affected:
        has_ui_file = any("screen" in f.lower() or "/ui/" in f.lower() for f in files_affected)
        if has_ui_file:
            return {
                "mode": "tweak" if has_existing_screen else "new_screen",
                "reason": (
                    "Affected files touch an existing UI screen — prefer targeted Stitch tweak if visual structure changes."
                    if has_existing_screen
                    else "Affected files indicate a new UI screen surface."
                ),
            }

    return {"mode": "bypass", "reason": "Default: Task does not require Stitch changes."}


def should_invoke_stitch(task_description: str, files_affected: list[str] | None = None) -> tuple[bool, str]:
    """Backwards-compatible wrapper over `recommend_stitch_mode`."""
    rec = recommend_stitch_mode(task_description, files_affected=files_affected)
    return rec["mode"] != "bypass", rec["reason"]


def curate_stitch_manifest(project: Path, screens_inventory: list[dict[str, Any]]) -> dict[str, Any]:
    """Maintain a generic, canonical manifest of Stitch screens (no hardcoded domain keywords).

    - Preserves already-approved canonical entries and exported file paths from
      `docs/stitch/manifest.json`.
    - Flags obvious scratch/draft experiments (`scratch`, `draft`, `untitled`, `tmp`,
      `deprecated`, `backup`) and duplicate older titles unless already approved.
    - Saves to `docs/stitch/manifest.json` AND `.pipeline/stitch_manifest.json`.
    """
    tracked_path = project / "docs" / "stitch" / "manifest.json"
    tracked_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path = pipeline_dir(project) / "stitch_manifest.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)

    existing_by_id: dict[str, dict[str, Any]] = {}
    existing_project_id = ""
    existing_synced_at = ""
    if tracked_path.is_file():
        try:
            old = json.loads(tracked_path.read_text())
            existing_project_id = str(old.get("project_id") or "")
            existing_synced_at = str(old.get("synced_at") or "")
            for s in old.get("canonical_screens") or []:
                if s.get("id"):
                    existing_by_id[str(s["id"])] = s
        except Exception:
            pass

    scratch_words = ("scratch", "draft", "untitled", "deprecated", "backup", "throwaway", "experiment")

    canonical_screens: list[dict[str, Any]] = []
    stale_screens: list[dict[str, Any]] = []
    seen_titles: dict[str, int] = {}

    for s in screens_inventory:
        title = str(s.get("title") or "").strip()
        raw_name = str(s.get("name") or "")
        screen_id = raw_name.split("/")[-1] if "/" in raw_name else str(s.get("id") or "")
        if not screen_id:
            continue
        t_low = title.lower()
        is_scratch = any(re.search(rf"\b{re.escape(w)}\b", t_low) for w in scratch_words)
        prev = existing_by_id.get(screen_id) or {}

        if is_scratch and screen_id not in existing_by_id:
            stale_screens.append({
                "id": screen_id,
                "title": title or screen_id,
                "status": "CANDIDATE_FOR_CLEANUP",
            })
            continue

        entry: dict[str, Any] = {
            "id": screen_id,
            "title": title or prev.get("title") or screen_id,
            "surface": prev.get("surface") or s.get("surface") or "",
            "type": s.get("deviceType") or prev.get("type") or "MOBILE",
            "status": "APPROVED",
        }
        screen_dir = project / "docs" / "stitch" / "screens" / screen_id
        if (screen_dir / "index.html").is_file():
            entry["html"] = f"screens/{screen_id}/index.html"
        elif prev.get("html"):
            entry["html"] = prev["html"]
        if (screen_dir / "screen.png").is_file():
            entry["screenshot"] = f"screens/{screen_id}/screen.png"
        elif prev.get("screenshot"):
            entry["screenshot"] = prev["screenshot"]

        norm_title = re.sub(r"\s+", " ", t_low)
        if norm_title and norm_title in seen_titles and screen_id not in existing_by_id:
            # Duplicate title: move the earlier non-pinned duplicate to stale unless pinned
            prev_idx = seen_titles[norm_title]
            prev_entry = canonical_screens[prev_idx]
            if prev_entry["id"] not in existing_by_id:
                stale_screens.append({
                    "id": prev_entry["id"],
                    "title": prev_entry["title"],
                    "status": "CANDIDATE_FOR_CLEANUP",
                })
                canonical_screens[prev_idx] = entry
                continue
        seen_titles[norm_title] = len(canonical_screens)
        canonical_screens.append(entry)

    profile = profile_of(project)
    pid = existing_project_id or str((profile.get("design") or {}).get("stitch_project_id") or "").split("/")[-1]
    manifest = {
        "project": project.name,
        "project_id": pid,
        "synced_at": existing_synced_at or now_iso(),
        "updated_at": now_iso(),
        "canonical_screens": canonical_screens,
        "stale_screens": stale_screens,
        "screen_count": len(canonical_screens),
        "stale_count": len(stale_screens),
    }
    save_json(tracked_path, manifest)
    save_json(legacy_path, manifest)
    return manifest


def extract_html_summary(html_text: str) -> dict[str, list[str]]:
    """Extract headings, interactive button/link labels, and nav labels from Stitch HTML."""
    if not html_text:
        return {"headings": [], "controls": []}
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_text, flags=re.S | re.I)

    def _strip_tags(s: str) -> str:
        import html as _html
        # Drop material symbol icon names wrapped in <span class="material-symbols...">
        s = re.sub(r'<span[^>]*material-symbols[^>]*>.*?</span>', " ", s, flags=re.S | re.I)
        s = re.sub(r"<[^>]+>", " ", s)
        s = _html.unescape(s)
        return re.sub(r"\s+", " ", s).strip()

    headings: list[str] = []
    for m in re.finditer(r"<h[1-4][^>]*>(.*?)</h[1-4]>", cleaned, flags=re.S | re.I):
        t = _strip_tags(m.group(1))
        if t and len(t) <= 80 and t not in headings:
            headings.append(t)

    controls: list[str] = []
    for m in re.finditer(r"<(?:button|a)[^>]*>(.*?)</(?:button|a)>", cleaned, flags=re.S | re.I):
        t = _strip_tags(m.group(1))
        if t and len(t) <= 60 and t not in controls:
            controls.append(t)

    return {"headings": headings[:12], "controls": controls[:20]}


def compile_stitch_tweak_prompt(
    project: Path,
    change_request: str,
    *,
    screen_title: str = "",
    screen_id: str = "",
    preserve: list[str] | None = None,
    baseline_html: str = "",
) -> str:
    """Compile a surgical, non-destructive tweak prompt for Stitch `edit_screens`."""
    profile = profile_of(project)
    name = profile.get("name") or project.name
    tokens = extract_design_tokens(project)
    summary = extract_html_summary(baseline_html)

    preserve_lines: list[str] = []
    for item in (preserve or []):
        if str(item).strip():
            preserve_lines.append(f"- {str(item).strip()}")
    if summary["headings"]:
        preserve_lines.append(f"- Existing headings/sections: {', '.join(summary['headings'][:6])}")
    if summary["controls"]:
        preserve_lines.append(f"- Existing interactive controls/CTAs (keep intact unless the tweak changes them): {', '.join(summary['controls'][:10])}")
    if not preserve_lines:
        preserve_lines.append("- Keep the existing spatial layout, navigation structure, typography, and color palette intact.")

    target_label = screen_title or screen_id or "Target Screen"
    preserve_block = "\n".join(preserve_lines)

    return f"""TARGET SCREEN: {target_label} ({name})
OPERATION: SURGICAL IN-PLACE TWEAK — DO NOT REDESIGN FROM SCRATCH.

REQUESTED CHANGE (apply ONLY this change):
{change_request.strip()}

PRESERVE EXISTING DESIGN (DO NOT REMOVE OR REINVENT):
{preserve_block}
- Keep the existing theme ({tokens.get('theme_mode', 'light')} ground {tokens['surface_base']}, elevated {tokens['surface_elevated']}, primary accent {tokens['primary_accent']}, typography {tokens['headline_font']} / {tokens['body_font']}).
- Do NOT add unrequested features (no sign-in/OAuth walls, no ads/upgrade banners, no extra nav bars, no search bars unless explicitly requested above).
- Do NOT use Lorem Ipsum or placeholder numbers where real labels are specified.
- Maintain >= 48dp touch targets, >= 4.5:1 text contrast, and clean 1px structural borders (no glowing neon blur halos).
"""


def _target_context_for_screen(project: Path, profile: dict, screen_type: str) -> dict[str, Any]:
    """Derive generic screen context from the project's own targets, PRD, and manifest."""
    st = screen_type.lower().strip()
    targets = [t for t in (profile.get("targets") or []) if isinstance(t, dict)]
    matched_target = None
    for t in targets:
        label = str(t.get("label") or "").lower()
        if st and (st == label or st in label):
            matched_target = t
            break

    prd_excerpt = ""
    prd_path = project / "docs" / "PRD.md"
    if prd_path.is_file():
        prd_text = prd_path.read_text(errors="ignore")
        lines = [ln.strip() for ln in prd_text.splitlines() if ln.strip() and not ln.startswith("#")]
        prd_excerpt = " ".join(lines[:6])[:400]

    return {"target": matched_target, "prd_excerpt": prd_excerpt}


def compile_stitch_screen_prompt(
    project: Path,
    screen_type: str = "main",
    spec: dict[str, Any] | None = None,
) -> str:
    """Compile an authoritative, project-generic screen prompt for Stitch MCP.

    Reads the project's own profile, targets, PRD, and extracted Stitch tokens —
    zero hardcoded domain/app examples in team code.
    """
    profile = profile_of(project)
    name = profile.get("name") or project.name
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    tokens = extract_design_tokens(project)
    arch = detect_domain_archetype(project)
    ctx = _target_context_for_screen(project, profile, screen_type)
    target = ctx.get("target") or {}

    if spec:
        screen_title = spec.get("title", f"{name} - {screen_type}")
        context_desc = spec.get("context", vision)
        layout_zones = list(spec.get("zones") or [])
        ctas = list(spec.get("ctas") or [])
        telemetry = dict(spec.get("telemetry") or {})
        aesthetic = spec.get("aesthetic", arch["aesthetic"])
    else:
        aesthetic = arch["aesthetic"]
        label = str(target.get("label") or screen_type).strip()
        screen_title = f"{name} - {label.replace('-', ' ').replace('_', ' ').title()}"
        target_notes = str(target.get("notes") or target.get("description") or "").strip()
        context_parts = [p for p in (vision, target_notes, ctx.get("prd_excerpt")) if p]
        context_desc = f"Screen '{label}' for {name}. " + (" ".join(context_parts) if context_parts else "")
        layout_zones = [
            f"1. Top Context Bar: Clear screen title for '{label}' and focused navigation without clutter.",
            f"2. Primary Hero / Focal Area: One clear visual anchor aligned with the screen's core user action on {tokens['surface_elevated']}.",
            f"3. Supporting Content / List Area: Structured, scannable hierarchy using {tokens['surface_base']} canvas and crisp 1px borders ({tokens['surface_highlight']}).",
            f"4. Primary Action Area: Thumb-accessible primary CTA in {tokens['primary_accent']} (>= 48dp touch target).",
        ]
        ctas = list(target.get("ctas") or [])
        telemetry = dict(target.get("telemetry") or {})

    zones_text = "\n".join(layout_zones)
    ctas_text = (
        "\n".join(f"- {c}" for c in ctas)
        if ctas
        else f"- Derive focused, domain-authentic interactive controls for '{label}' (no unrequested auth, ads, or search bars)."
    )
    telemetry_text = (
        "\n".join(f"- {k}: {v}" for k, v in telemetry.items())
        if telemetry
        else "- Use realistic domain-authentic labels and values from the app's real state (no Lorem Ipsum)."
    )
    theme_mode = tokens.get("theme_mode") or arch.get("theme_mode", "light")

    return f"""Title: {screen_title}
Device: Mobile (390px viewport width, portrait)
Platform Aesthetic: {aesthetic}

=== SECTION 1: PRODUCT INTENT & CONTEXT ===
{context_desc}

=== SECTION 2: PRE-LOCKED INTERACTION ARCHITECTURE & EXACT CTAs ===
All functional CTAs and interaction states are locked upfront. Realize this exact flow without omitting or inventing features:
{ctas_text}

=== SECTION 3: REAL-WORLD COPY & TELEMETRY (NO LOREM IPSUM) ===
{telemetry_text}

=== SECTION 4: SCREEN LAYOUT & SPATIAL ZONES ===
{zones_text}

=== SECTION 5: STRICT DESIGN SYSTEM & ANTI-AI CRAFTSMANSHIP ===
- Theme Mode: {theme_mode.upper()} (token source: {tokens.get('source', 'archetype')})
- Canvas Base: {tokens['surface_base']}
- Card Containers: {tokens['surface_elevated']} with crisp 1px structural borders ({tokens.get('surface_highlight', '#E2E8F0')})
- Primary Accent: {tokens['primary_accent']} for primary interactive actions and selections
- Secondary Accent: {tokens['secondary_accent']} for secondary chips and category tags
- Typography Scale:
  * Headlines & Hero Numerals: {tokens['headline_font']} (tabular spacing for live numbers)
  * Body & Section Titles: {tokens['body_font']}
  * Labels & Pills: {tokens['label_font']}

- STRICT ANTI-AI MANDATES:
  * NO GLOWING NEON HALOS OR RADIANT AURAS around cards or buttons; use clean 1px hairline borders and soft diffuse shadows.
  * NO WALL OF IDENTICAL CARDS: create clear visual hierarchy with one primary focal point.
  * NO PSEUDO-TECH BADGE SPAM or unrequested auth/ads/search bars.
  * NO LOW-CONTRAST TEXT: maintain >= 4.5:1 WCAG AA contrast against the ground ({tokens['surface_base']}).
  * NO CRAMPED TOUCH TARGETS: every interactive target >= 48dp x 48dp.
  * {arch['anti_patterns']}
"""

