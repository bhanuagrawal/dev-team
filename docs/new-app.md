# New app, in one go — the non-negotiable order

Loaded by: `devteam onboard` · `skills/new-project/SKILL.md`.

## New App "In One Go" — Non-Negotiable Order & Hard Checklist

When building and shipping a brand-new app MVP in one go, the agent MUST execute these steps in strict sequence before returning to the owner that work is done:

1. **Discovery & Architecture:**
   - Draft `docs/PRD.md` with explicit, domain-specific features (no generic placeholders).
   - Perform competitive recon and write `docs/COMPETITION_RESEARCH.md` (minimum 5 competitors with pricing, weaknesses, and differentiation).
2. **Initial UI Design & Stitch MCP:**
   - Initial UI is designed via **Stitch MCP** (`generate_screen_from_text` or `create_design_system_from_design_md`), NEVER invented blindly.
   - Design tokens, typography, and color palette written into `docs/design.md`.
3. **Single Source of Truth Plan (the goal tree, not a file):**
   - The plan is the tree in `.pipeline/{profile,state}.json`: vision → goals → milestones → the queue. Create it with `devteam vision` + the planner, never by hand-writing a markdown plan — `docs/plan.md` was a second copy that drifted and is retired.
   - Run `devteam adapt <project>` to sync the tree, and `devteam publish <project>` to put its one published page (`docs/goals.html`, served by GitHub Pages) on `main`.
4. **Code Implementation:**
   - 100% native platform code (Compose / Kotlin).
   - Real offline persistence, real domain logic (no mocks, no fake static data).
   - All screens adhere to `docs/design.md` tokens.
6. **Services & Telemetry:**
   - Firebase: Project created, SHA-1 and SHA-256 registered, `google-services.json` committed.
   - GA4: Property and Data Stream created, linked to app package.
   - Privacy Policy: `docs/privacy.html` committed and verified live on public HTTPS URL (HTTP 200).
7. **Store Distribution & Review:**
   - Build & sign AAB bundle via canonical CI (`release.yml`).
   - Upload AAB to Play Console internal testing track.
   - Fill Play Store listing copy and upload all 6 authentic visual assets (Icon, Feature Graphic, 4 Posters).
   - Save changes and submit for Google Play review ("Changes in review").
   - Obtain internal testing join link (`https://play.google.com/apps/internaltest/...`).
8. **Automated Sanity Gates (Code-Evaluated):**
   - `python3 cli.py store-readiness <project>` → PASS (evaluates Play compliance, SDK targets, assets, and active `launchd` nightly schedule).
   - `python3 cli.py doctor <project>` → PASS (evaluates orchestrator readiness, launchd registration, CI parity, and git tree clean).
   - Both git repos (`dev-team` and `<project>`) are 100% clean with all commits pushed to origin.

NEXT: `skills/new-project/SKILL.md` (Mode B) — this page is the order, that skill
is the doing.
