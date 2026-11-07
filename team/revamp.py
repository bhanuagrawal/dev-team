"""Full-spectrum app revamp and ground-up rebuild engine for dev-team.

Ensures that when an owner requests an app revamp or rebuild from scratch:
1. Anchors strictly to the immutable long-term vision (`profile.goals.vision`).
2. Re-imagines the core loop, product architecture, and monetization via `docs/PRD.md`.
3. Runs deep competitive recon via `team.competition` -> `docs/COMPETITION_RESEARCH.md`.
4. Synthesizes a domain-differentiated design system via `team.design` -> `docs/design.md`.
5. Mechanically purges and archives obsolete legacy UI layers so old composables cannot survive.
6. Invalidates stale pre-revamp marketing caches (icons, posters, banners) to force fresh asset creation.
7. Keeps both the project repository and pipeline synchronized.

Zero project names in code.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from . import adapt, competition, design
from .core import RunLog, now_iso, pipeline_dir, profile_of, save_json

PRD_DOC_REL = "docs/PRD.md"


def invalidate_storefront_assets(project: Path, log: RunLog | None = None) -> list[str]:
    """Purge cached storefront assets to force 100% fresh regeneration after a revamp."""
    cleared = []
    p_dir = pipeline_dir(project)

    # Invalidate brand cache
    brand_dir = p_dir / "storefront_brand"
    if brand_dir.is_dir():
        for f in brand_dir.glob("raw-*.png"):
            try:
                f.unlink()
                cleared.append(str(f.relative_to(project)))
            except OSError:
                pass
        approved_json = brand_dir / "approved_brand.json"
        if approved_json.is_file():
            try:
                approved_json.unlink()
                cleared.append(str(approved_json.relative_to(project)))
            except OSError:
                pass

    # Invalidate scenes cache
    scenes_dir = p_dir / "storefront_scenes"
    if scenes_dir.is_dir():
        for f in scenes_dir.glob("raw-*.png"):
            try:
                f.unlink()
                cleared.append(str(f.relative_to(project)))
            except OSError:
                pass

    if log:
        log(f"~ invalidated {len(cleared)} stale storefront assets/caches")
    return cleared


def purge_legacy_ui(project: Path,
                     target_tree: Path | None = None,
                     log: RunLog | None = None) -> list[str]:
    """Safely purge and archive legacy UI screens and UI tests."""
    return purge_codebase(project, target_tree=target_tree, full_codebase=False, log=log)


def purge_codebase(project: Path,
                   target_tree: Path | None = None,
                   full_codebase: bool = True,
                   log: RunLog | None = None) -> list[str]:
    """Safely discover and archive legacy application code and tests.

    When an owner requests a revamp, rebuild from scratch, or codebase deletion,
    this mechanically removes the entire legacy application codebase (main Kotlin/Java,
    unit tests, and instrumented tests) into .pipeline/archived_codebase/<timestamp>.

    Prevents developer agents from lazily modifying existing wrappers or keeping
    zombie architectures alive when a ground-up rebuild was ordered.
    Preserves signing keys, keystore.properties, google-services.json, and build setup.
    """
    root = target_tree if (target_tree and target_tree.is_dir()) else project
    archived = []
    timestamp = now_iso().replace(":", "-").replace(".", "-")
    archive_dest = pipeline_dir(project) / "archived_codebase" / timestamp

    # Search for all source directories across the project
    search_dirs: list[Path] = []
    if full_codebase:
        # Full codebase purge: all main code, unit tests, and instrumented tests
        for pattern in ("**/src/main/java", "**/src/main/kotlin",
                        "**/src/test/java", "**/src/test/kotlin",
                        "**/src/androidTest/java", "**/src/androidTest/kotlin"):
            search_dirs.extend(root.glob(pattern))
    else:
        # UI-only legacy purge fallback
        search_dirs.extend(root.glob("app/src/main/java/**/ui"))
        if not search_dirs:
            search_dirs.extend(root.glob("**/ui"))
        for p in ("app/src/androidTest/java", "app/src/androidTest/kotlin", "**/androidTest/**"):
            search_dirs.extend(root.glob(p))

    seen_files: set[Path] = set()
    for s_dir in search_dirs:
        # Don't touch build directories or .pipeline inside the target root
        rel_dir = s_dir.relative_to(root)
        if ".pipeline" in rel_dir.parts or "build" in rel_dir.parts:
            continue
        for src_file in s_dir.glob("**/*"):
            if not src_file.is_file():
                continue
            if src_file in seen_files:
                continue
            seen_files.add(src_file)

            # Only purge code files (.kt, .java)
            if src_file.suffix not in (".kt", ".java"):
                continue

            rel_path = src_file.relative_to(root)

            # If not full codebase, only target UI/screen/component files and UI tests
            if not full_codebase:
                is_theme = any(t in src_file.name.lower() for t in ["theme", "color", "type", "shape"])
                is_ui_screen = any(s in src_file.name.lower() for s in ["screen", "view", "page", "dialog", "component", "card", "bar"])
                is_ui_test = "androidtest" in str(src_file).lower() or "screentest" in src_file.name.lower()
                if is_theme or not (is_ui_screen or is_ui_test):
                    continue

            dest_file = archive_dest / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest_file)
            archived.append(str(rel_path))
            try:
                src_file.unlink()
            except OSError:
                pass

    # Clean up empty parent directories
    for s_dir in search_dirs:
        if s_dir.is_dir():
            for dirpath, dirnames, filenames in os.walk(s_dir, topdown=False):
                if not dirnames and not filenames:
                    try:
                        os.rmdir(dirpath)
                    except OSError:
                        pass

    if log:
        log(f"~ purged & archived {len(archived)} legacy code files to {archive_dest.relative_to(project)}")
    return archived


def synthesize_reimagined_prd(project: Path,
                               category: str = "",
                               force: bool = False,
                               log: RunLog | None = None) -> Path:
    """Synthesize docs/PRD.md anchoring to profile vision with re-imagined product loops."""
    prd_file = project / PRD_DOC_REL
    if prd_file.is_file() and not force:
        if log:
            log(f"~ {PRD_DOC_REL} already exists (skipping synthesis, force=False)")
        return prd_file

    profile = profile_of(project)
    name = profile.get("name") or project.name
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    arch = design.detect_domain_archetype(project)

    content = f"""# Product Requirements Document (PRD): {name} (Re-imagined Architecture)

> **Authority:** Registered under `.pipeline/profile.json` `conventions`.  
> **Status:** ACTIVE RE-IMAGINED ARCHITECTURE  
> **Last Updated:** `{now_iso()}`  
> **Design Domain Archetype:** `{arch['domain']}`

---

## 1. Executive Vision & Core Thesis

**Immutable Long-Term Vision:**  
`{vision or f"Establish {name} as the category-defining, distraction-free benchmark."}`

**The Re-imagined Product Strategy:**  
Rather than competing on endless feature bloat or aggressive subscription paywalls, {name} wins by delivering a 10x more responsive, private, and tactile core utility loop. Every screen adheres strictly to the `{arch['domain']}` design laws:
- 100% on-device local execution (zero cloud telemetry, zero remote tracking).
- Sub-16ms tactile 60/120fps UI response powered by Jetpack Compose.
- Single-minded clarity: Eliminating secondary friction in favor of immediate utility.

---

## 2. Target User Personas & Pain Points

| Persona | Core Need | Market Incumbent Flaw Solved |
|---|---|---|
| **The Intentionalist** | Wants fast, frictionless execution without dopamine loops | Incumbent apps inject ads and notification bait |
| **The Privacy Purist** | Demands local on-device processing | Competitors demand account logins and sync telemetry |
| **The Power Minimalist** | Values glanceability, battery health, and speed | Bloated rivals take 2-4 seconds to cold start |

---

## 3. The Re-imagined 3-Step Core Loop

```mermaid
flowchart LR
    A["1. Launch & Instant Context"] --> B["2. Tactile Core Action (<3 taps)"]
    B --> C["3. Crisp Haptic Confirmation & Return"]
```

1. **Phase 1: Instant Intentional Launch**  
   Cold-start directly into the primary utility surface. Zero splash delays, zero onboarding carousels, zero sign-in walls.
2. **Phase 2: Tactile High-Contrast Action**  
   Core operations are performed directly on the primary surface with high-contrast `{arch['primary_accent']}` touch targets (minimum 48dp). Dynamic metrics use tabular monospace numerals.
3. **Phase 3: Immediate Haptic Feedback & Return**  
   Every state modification triggers physical haptic feedback and fluid spring transitions.

---

## 4. Feature Architecture & Surface Specifications

### Surface 1: Primary Hub Screen
- **Context:** Distraction-free, OLED-dark base canvas (`{arch['surface_base']}`).
- **Key Interactivity:** Primary hero utility card framed by `{arch['primary_accent']}` focus aura.
- **Interaction Rules:** Maximum 2 taps to complete any primary operation.

### Surface 2: Focus Configuration & Control Studio
- **Context:** Tactile configuration deck using elevated slate containers (`{arch['surface_elevated']}`).
- **Key Interactivity:** Discrete stepped sliders and segmented selectors with live preview telemetry.

### Surface 3: Session Analytics & History
- **Context:** Tabular breakdown of usage telemetry, session milestones, and performance efficiency.

---

## 5. Monetization & Fair Exchange Policy

- **The Core Loop is Sacred:** The primary utility workflow is 100% free and private forever.
- **Zero Extortion Paywalls:** No time-bombed 7-day trials that silently convert to $49/yr subscriptions.
- **Optional Patron / Power Pack:** An optional one-time purchase ($4.99–$9.99) unlocking advanced theme customization and extended telemetry archives.

---

## 6. Technical & Non-Functional Requirements

- **SDK Baseline:** `minSdk = 26` (Android 8.0+), `targetSdk = 35` (Android 15+).
- **Architecture:** MVVM + Clean Architecture + StateFlow/Compose.
- **Persistence:** Local Android Room SQLite or DataStore (100% offline).
- **Haptics:** Android `HapticFeedbackConstants` wired to every primary CTA.
- **Zero Third-Party Ad Trackers:** Clean privacy footprint.
"""
    prd_file.parent.mkdir(parents=True, exist_ok=True)
    prd_file.write_text(content, encoding="utf-8")
    if log:
        log(f"✓ synthesized {PRD_DOC_REL} ({len(content)} bytes)")
    return prd_file


def run_revamp(project: Path,
               focus: str = "",
               target_tree: Path | None = None,
               force: bool = True,
               log: RunLog | None = None) -> dict[str, Any]:
    """Execute complete ground-up revamp coordination for a project."""
    if log:
        log(f"=== Starting Ground-Up Revamp for {project.name} ===")

    # 1. Synthesize re-imagined PRD anchored to vision
    prd_path = synthesize_reimagined_prd(project, category=focus, force=force, log=log)

    # 2. Run competitive research
    comp_report = competition.research_competitors(project, force=force, log=log)

    # 3. Synthesize domain-differentiated design system
    design_report = design.ensure_design(project, topic=focus, force=force, log=log)

    # 4. Invalidate stale marketing assets
    cleared_assets = invalidate_storefront_assets(project, log=log)

    # 5. Purge legacy codebase if target tree is supplied
    purged_code = []
    if target_tree:
        purged_code = purge_codebase(project, target_tree=target_tree, full_codebase=True, log=log)

    # 6. Adapt project to sync pipeline state
    adapt.run(project, log=log)

    res = {
        "status": "ready_for_rebuild",
        "project": project.name,
        "prd": str(prd_path.relative_to(project)),
        "competition": comp_report.get("status"),
        "design": design_report.get("status"),
        "invalidated_assets": len(cleared_assets),
        "purged_legacy_code_count": len(purged_code),
        "next_steps": [
            f"1. Start worktree: `devteam worktree-start {project.name} --task-id revamp-core-loop`",
            f"2. Build native screens following docs/design.md tokens",
            f"3. Verify: `devteam verify {project.name} --worktree <wt> --task-spec .pipeline/tasks/revamp-core-loop.json`",
            f"4. Release: `devteam release {project.name} --worktree <wt> --task-spec .pipeline/tasks/revamp-core-loop.json`",
            f"5. Fresh brand & posters: `devteam brand {project.name} --design --publish --yes`",
            f"6. Fresh posters: `devteam storefront-build {project.name} --poster --force && devteam store-publish {project.name} --yes`",
        ],
    }
    if log:
        log(f"✓ Revamp preparation complete for {project.name}")
    return res
