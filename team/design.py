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


def _category_query(project: Path, topic: str = "") -> str:
    """Generate search query for Tavily market & UX research."""
    if topic.strip():
        return f"{project.name} {topic.strip()} modern mobile app UI UX design system color tokens typography 2026"
    profile = profile_of(project)
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    return f"{project.name} {vision} modern mobile app UI UX design system color tokens typography layout dark mode standards 2026"


def _build_design_template(project_name: str,
                           vision: str,
                           category_summary: str,
                           sources: list[str]) -> str:
    """Synthesize an authoritative docs/design.md specification."""
    sources_md = "\n".join(f"- {s}" for s in sources[:4]) if sources else "- Official Google Android / Material 3 2026 Guidelines"

    return f"""# {project_name} Design System & Visual Specification (2026 Edition)

> **Authority:** This document is registered under `.pipeline/profile.json` `conventions`.  
> Per pipeline law, this is a **MANDATORY READ** for all developer and UI agents. Doctrine governs new files; for edited files, match and elevate to this standard.

---

## 1. Product Philosophy & Visual Metaphor

**Core Thesis:** {vision or f"Elevate {project_name} to category-defining visual polish and usability."}

- **Visual Metaphor:** The interface is focused, purposeful, and tactile. Every screen must respect the user's attention, eliminating cognitive friction and visual noise.
- **Aesthetic Tone:** High contrast, deliberate spatial hierarchy, polished surfaces, and fluid micro-interactions. No muddy greys, no low-contrast text, no unpadded tap targets.
- **Category Benchmark & Research Insights:**
{category_summary or "Modern 2026 mobile design emphasizes dark-first high-contrast palettes, tabular numerals for dynamic metrics, minimum 48dp touch targets, and full-bleed gesture-friendly surfaces."}

---

## 2. Color Palette & Token Architecture

The app adopts a **Dark-First, High-Contrast OLED** theme optimized for clarity, battery efficiency, and glanceability.

| Token | Hex | Role | Contrast / Usage |
|---|---|---|---|
| `SurfaceBase` | `#0A0E14` | True obsidian canvas base | Deep background canvas |
| `SurfaceElevated` | `#161F2E` | Container surface (Level 1) | Primary content cards / tool panels |
| `SurfaceElevatedHigh` | `#202B3C` | Highlighted surface (Level 2) | Active cards, dialogs, bottom sheets |
| `BrandPrimary` | `#00E5FF` | Electric Cyan / Primary accent | Primary CTAs, active selections, focus rings |
| `BrandSecondary` | `#7C4DFF` | Vivid Violet / Secondary accent | Badges, secondary toggles, progress fills |
| `AccentSuccess` | `#00E676` | Neon Emerald | Completed states, success confirmations |
| `AccentWarning` | `#FFB703` | Amber Gold | Destructive confirmations, alerts, limits |
| `AccentError` | `#FF3D71` | Vivid Coral | Critical errors, validation failures |
| `TextPrimary` | `#FFFFFF` | Primary numerals & headings | 100% white, maximum contrast (WCAG AAA) |
| `TextSecondary` | `#94A3B8` | Subtitles, labels, metadata | Slate grey, minimum 4.5:1 contrast (WCAG AA) |
| `TextTertiary` | `#64748B` | Disabled indicators, dividers | Subtle borders and inactive outlines |

---

## 3. Typography Scale & Legibility

All typography must adhere to Jetpack Compose / Material 3 type tokens. Dynamic numerical metrics must always use **Tabular Numbers** (`FontFeatureSettings("tnum")`) to avoid layout jitter during value updates.

```kotlin
object DesignTypography {{
    // Hero Numerals & Display Metrics
    val heroMetric = TextStyle(
        fontSize = 72.sp,
        fontWeight = FontWeight.Black,
        letterSpacing = (-1.5).sp
    )
    val titleLarge = TextStyle(
        fontSize = 24.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = 0.sp
    )
    val titleMedium = TextStyle(
        fontSize = 18.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 0.15.sp
    )
    val bodyLarge = TextStyle(
        fontSize = 16.sp,
        fontWeight = FontWeight.Normal,
        lineHeight = 24.sp
    )
    val bodyMedium = TextStyle(
        fontSize = 14.sp,
        fontWeight = FontWeight.Normal,
        lineHeight = 20.sp
    )
    val labelSmall = TextStyle(
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

    content = _build_design_template(
        project_name=project.name,
        vision=vision,
        category_summary=answer,
        sources=sources
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
    """Extract authoritative design tokens from docs/design.md."""
    tokens = {
        "surface_base": "#0A0E14",
        "surface_elevated": "#161F2E",
        "surface_highlight": "#202B3C",
        "primary_accent": "#00E676",
        "secondary_accent": "#FF3D71",
        "warning_accent": "#FFB703",
        "text_primary": "#FFFFFF",
        "text_secondary": "#8B949E",
        "headline_font": "Anton",
        "body_font": "Barlow Condensed",
        "label_font": "JetBrains Mono",
    }
    design_doc = project / "docs/design.md"
    if design_doc.is_file():
        import re
        text = design_doc.read_text(errors="ignore")
        for line in text.splitlines():
            line = line.strip()
            m = re.search(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", line)
            hex_val = m.group(0)[:7] if m else None
            if not hex_val:
                continue
            if "SurfaceBase" in line or "background:" in line.lower():
                tokens["surface_base"] = hex_val
            elif "SurfaceElevated" in line or "surface:" in line.lower():
                tokens["surface_elevated"] = hex_val
            elif "SurfaceElevatedHigh" in line or "surface-high:" in line.lower():
                tokens["surface_highlight"] = hex_val
            elif "ServingAccent" in line or "accent:" in line.lower() or "BrandPrimary" in line or "AccentSuccess" in line:
                tokens["primary_accent"] = hex_val
            elif "ReceivingAccent" in line or "accent-coral:" in line.lower() or "AccentError" in line:
                tokens["secondary_accent"] = hex_val
            elif "warning:" in line.lower() or "AccentWarning" in line:
                tokens["warning_accent"] = hex_val
    return tokens


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

    if spec:
        screen_title = spec.get("title", f"{name} Screen")
        context_desc = spec.get("context", vision)
        layout_zones = spec.get("zones", [])
        ctas = spec.get("ctas", [])
        telemetry = spec.get("telemetry", {})
    elif st in ("score", "scoreboard", "main", "core"):
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
    elif st in ("summary", "history", "stats", "recap"):
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
    elif st in ("setup", "config", "new"):
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
Platform Aesthetic: High-adrenaline dark-first sports-tech UI

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
- Canvas Base: True obsidian background ({tokens['surface_base']}) with subtle domain texture (e.g. textured court lines, net cues).
- Card Containers: Elevated dark slate ({tokens['surface_elevated']}) with crisp structural borders ({tokens.get('surface_highlight', '#202B3C')}).
- Active Radiant Aura: The currently active or serving state MUST feature a glowing neon emerald aura ({tokens['primary_accent']}) so users recognize the state instantly from across a court without reading words.
- Secondary Accent: Use vibrant coral ({tokens['secondary_accent']}) exclusively for turnovers, faults, and side-out transitions.
- Warning / Metric Accent: Use warm amber ({tokens['warning_accent']}) for configuration chips and duration telemetries.
- Typography Scale:
  * Numbers & Metric Heroes: Massive {tokens['headline_font']} numerals with tabular spacing.
  * Headings & Team Names: Condensed high-energy {tokens['body_font']}.
  * Status Badges & Pills: Monospaced {tokens['label_font']} with uppercase letter-spacing.
- ANTI-PATTERNS (STRICTLY FORBIDDEN):
  * NO generic white card templates or muddy grey backgrounds.
  * NO low-contrast grey-on-dark text (all secondary text must maintain 4.5:1 WCAG AA contrast).
  * NO cramped interactive touch targets (every tap zone must be min 48dp x 48dp).
  * NO generic floating action buttons (FABs) obscuring content.
"""

