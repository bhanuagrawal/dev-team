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
