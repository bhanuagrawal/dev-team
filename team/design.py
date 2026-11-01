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
    """Extract authoritative design tokens from docs/design.md.

    The token table is `| `BrandPrimary` | `#E8442A` | role |`, so the NAME cell is
    matched exactly. A substring test on the whole line is wrong and was observed
    wrong: `OnBrandPrimary` (the colour of text ON the accent) contains
    `BrandPrimary`, so it overwrote it — every generated poster, icon and Stitch
    prompt was then seeded with a near-black "accent".
    """
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
        # Standard canonical UI terms or vocabulary from the project's own name and PRD
        import re
        proj_tokens = set(re.findall(r"[a-z]{3,}", project.name.lower()))
        try:
            prof = profile_of(project)
            proj_tokens |= set(re.findall(r"[a-z]{3,}", ((prof.get("goals") or {}).get("vision") or "").lower()))
        except Exception:
            pass

        standard_screen_tokens = [
            "icon", "banner", "feature graphic", "storefront", "home", "main", "core",
            "dashboard", "detail", "action", "inspect", "history", "summary", "stats",
            "setup", "config", "settings", "workspace", "studio", "viewer", "editor",
            "design.md"
        ]
        is_canonical_core = (any(tok in t_low for tok in standard_screen_tokens) or any(tok in t_low for tok in proj_tokens)) and not is_scratch

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


def _synthesize_screen_spec(project: Path, screen_type: str) -> dict[str, Any]:
    """Dynamically synthesize screen spec from docs/PRD.md, docs/design.md, or profile.json."""
    import re

    profile = profile_of(project)
    name = profile.get("name") or project.name
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    tokens = extract_design_tokens(project)

    st = screen_type.lower().strip()

    # Read PRD or design.md for domain features
    prd_text = ""
    for cand in (project / "docs/PRD.md", project / "ROADMAP.md", project / "docs/design.md"):
        if cand.is_file():
            prd_text += "\n" + cand.read_text(errors="ignore")

    features: list[str] = []
    if prd_text:
        bullets = re.findall(r"(?:^|\n)\s*(?:\d+\.|\*|-)\s+\*\*([^*]+)\*\*:\s*([^\n]+)", prd_text)
        if bullets:
            features = [f"{b[0].strip()}: {b[1].strip()}" for b in bullets]
        else:
            simple_bullets = re.findall(r"(?:^|\n)\s*(?:\d+\.|\*|-)\s+([^\n]+)", prd_text)
            features = [b.strip() for b in simple_bullets if len(b.strip()) > 5][:6]

    primary_feature = features[0] if features else f"{name} Core Flow"
    secondary_features = features[1:4] if len(features) > 1 else [f"{name} Action", "Data Inspection", "Settings"]

    aesthetic = f"High-precision OLED dark-first {name} UI with high-contrast tactile surfaces"

    if st in ("main", "home", "hub", "core", "dashboard"):
        screen_title = f"{name} - Central Workspace (Home)"
        context_desc = f"Primary central workstation for {name}. High-contrast OLED dark mode with glowing accents, tactile cards, and zero bloat. {vision}"
        hero_tag = primary_feature.split(":")[0] if ":" in primary_feature else primary_feature
        layout_zones = [
            f"1. Top Context Header: Wordmark '{name.upper()}' in {tokens['headline_font']}, glowing trust pill '100% OFFLINE • PRIVATE' with green status dot, and action button.",
            f"2. Primary Featured Hero Card: '{hero_tag}' hero action card with radiant {tokens['primary_accent']} border glow, category badge, and prominent primary launch CTA button.",
            f"3. Core Action Shelf / Tools Grid: Elevated slate cards ({tokens['surface_elevated']}) with rounded corners, high-contrast typography, and quick-action triggers:",
        ]
        for f in secondary_features:
            layout_zones.append(f"   - {f}")
        layout_zones.extend([
            f"4. Recent Activity / Work Deck: Technical activity items showing name, status badge, metadata tags, and action buttons.",
            f"5. Grounded Dock Bar: Fixed glassmorphic bottom bar with Home, Workspace, History, and Settings.",
        ])
        ctas = [
            f"Launch {hero_tag} (Primary Hero CTA -> Starts primary task)",
            "Review History / Recent Items",
            "Open Settings & Diagnostics",
        ]
        telemetry = {
            "Status": "Ready • Offline",
            "Engine": f"{name} Local Core",
            "Records": "Synchronized locally",
        }
    elif st in ("detail", "action", "inspect", "editor", "process", "workflow"):
        screen_title = f"{name} - Focused Action & Telemetry Studio"
        context_desc = f"Active task execution and data inspection studio for {name}: {primary_feature}."
        layout_zones = [
            f"1. Top Navigation Bar: Back button, screen title '{st.upper()}', status badge 'ACTIVE'.",
            f"2. Live Telemetry & Inspection Card: Elevated slate container ({tokens['surface_elevated']}) displaying primary metrics, real-time indicators, and parameter controls.",
            f"3. Interactive Control Deck: High-contrast toggles, segmented options, and tactile input fields styled with {tokens.get('surface_highlight', '#202B3C')} borders.",
            f"4. Primary Execution Action: Full-width high-impact CTA button in solid {tokens['primary_accent']} with luminous glow shadow.",
            f"5. Security & Verification Footer: '100% On-Device Processing • Local Private Storage'.",
        ]
        ctas = [
            "Back Button (Returns to Workspace)",
            "Toggle Parameter / Action Setting",
            f"Execute {st.capitalize()} (Primary CTA -> Confirms action)",
        ]
        telemetry = {
            "Active Task": primary_feature.split(":")[0] if ":" in primary_feature else primary_feature,
            "State": "Verified",
            "Latency": "Instant (0ms on-device)",
        }
    elif st in ("summary", "history", "stats", "records", "recap"):
        screen_title = f"{name} - History & Performance Summary"
        context_desc = f"Historical record review and verified summary breakdown for {name}."
        layout_zones = [
            f"1. Top Navigation Bar: Back button, title 'HISTORY & SUMMARY', search filter icon.",
            f"2. High-Impact Hero Summary Card: Highlighted key outcome, total records completed, and efficiency metric in {tokens['headline_font']} numerals.",
            f"3. Structured Record Stream: Multi-row list with status pills (Pass/Complete/Warning) in {tokens['surface_elevated']}.",
            f"4. Action Deck: 'EXPORT / SHARE REPORT' primary button with {tokens['primary_accent']} accent glow.",
        ]
        ctas = [
            "Filter History Records",
            "View Record Details (Opens inspection inspector)",
            "Export Summary PDF / Share (Primary CTA)",
        ]
        telemetry = {
            "Total Completed": "12",
            "Success Rate": "100%",
            "Storage": "Local encrypted database",
        }
    else:
        screen_title = f"{name} - {screen_type.capitalize()} Studio"
        context_desc = f"User interface surface for {name}: {vision}"
        layout_zones = [
            f"1. Top Context Bar: Clear screen title and navigation.",
            f"2. Primary Content Section: Structured modular cards using {tokens['surface_elevated']}.",
            f"3. Action Controls: High-contrast thumb-accessible buttons using {tokens['primary_accent']}.",
        ]
        ctas = ["Primary Action Button", "Secondary Action Option"]
        telemetry = {"Status": "Active • Verified"}

    return {
        "title": screen_title,
        "context": context_desc,
        "zones": layout_zones,
        "ctas": ctas,
        "telemetry": telemetry,
        "aesthetic": aesthetic,
    }


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
    tokens = extract_design_tokens(project)
    if not spec:
        spec = _synthesize_screen_spec(project, screen_type)

    screen_title = spec.get("title", f"{project.name} Screen")
    context_desc = spec.get("context", "")
    layout_zones = spec.get("zones", [])
    ctas = spec.get("ctas", [])
    telemetry = spec.get("telemetry", {})
    aesthetic = spec.get("aesthetic", "High-precision dark-first UI")

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
- Canvas Base: True obsidian background ({tokens['surface_base']}) with subtle atmospheric lighting.
- Card Containers: Elevated dark slate ({tokens['surface_elevated']}) with crisp structural borders ({tokens.get('surface_highlight', '#202B3C')}).
- Active Radiant Aura: The currently active action state MUST feature a glowing radiant aura ({tokens['primary_accent']}) so users recognize interactive focus instantly.
- Secondary Accent: Use secondary accent ({tokens['secondary_accent']}) for badges, progress fills, and status transitions.
- Warning / Metric Accent: Use warm accent ({tokens['warning_accent']}) for alert confirmations, limits, and telemetry.
- Typography Scale:
  * Numbers & Metric Heroes: Bold {tokens['headline_font']} numerals with tabular spacing.
  * Headings & Section Titles: Modern technical {tokens['body_font']}.
  * Status Badges & Pills: Monospaced {tokens['label_font']} with uppercase letter-spacing.
- ANTI-PATTERNS (STRICTLY FORBIDDEN):
  * NO generic white card templates or muddy grey backgrounds.
  * NO low-contrast grey-on-dark text (all secondary text must maintain 4.5:1 WCAG AA contrast).
  * NO cramped interactive touch targets (every tap zone must be min 48dp x 48dp).
  * NO generic floating action buttons (FABs) obscuring content.
"""
