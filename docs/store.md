# The Play listing — assets are deliverables

Loaded by: `devteam storefront-build` / `store-publish` (their refusal prints it).

5. **Store assets are deliverables.** Screenshots come from the app's own
   composables (`devteam storefront-scenes`), never from hand-drawn HTML. A
   generated artefact a user reads (store note, screenshots, release text) needs
   the consumer's constraints and an owner who can overwrite it. The real render is
   the **input** to the marketing image: `storefront-build --poster` attaches the
   scene's rendered screen and has **Gemini paint the whole asset in the browser,
   one prompt per scene** (`--poster`), then strips its watermark and upscales to a
   size Play accepts. The heal is strict: the corner the prompt keeps plain is
   re-checked as a contract, a frame whose corner still shows the model's mark is
   refused (never shipped with a warning), and the verdict is recorded per frame
   and re-read at publish. The prompt also asks the model to match the frames it
   has already painted in that chat — a soft instruction, not a gate. `--poster` is the DEFAULT of `storefront-build`: a
   code-composed frame (`--builtin`, `--gemini`/`--plate`, `--frame-html`,
   `--layout`, `--frame`) is a fallback and must carry `--why "<what must stay
   pixel-crisp>"`; the reason is recorded in `docs/storefront-frames.json` and
   `store-publish` refuses a listing whose frames are code-composed without one,
   or that has no recorded composition path at all (`storefront-build --plan`
   prints the path a run would take, without painting anything). A long pass keeps
   a run record (`.pipeline/storefront_run.json`: pid, step, verdict), so a run an
   agent backgrounded and abandoned reads as `interrupted at step '<x>'` rather
   than vanishing; `devteam storefront-status <project>` is how anyone — owner or
   orchestrator — checks what actually happened, and `store-publish` refuses an
   interrupted run outright. Never take an agent's word that a long command
   finished: read that record. The two other brand assets follow the same rule with
   different makers: the **icon is a Stitch design**, adopted from the artboard it
   returns (`storefront-brand --stitch-prompt` → `--adopt-stitch`), and the feature
   graphic is painted by Gemini from the hero screen. A listing whose icon or banner
   has no recorded provenance is refused, not graded. The code-rendered poster stays for work whose type must be
   pixel-crisp: Stitch (`--design-frame`/`--adopt-frame`), the vision model
   (`--vision-frame`), or a Gemini backdrop plate (`--gemini`/`--backdrop`) with
   every letter drawn by us. An image model repaints what it is shown, so a Gemini
   poster is a **depiction** of the app, never proof of it — say so, and never let
   a frame assert a feature or number the app does not have.
   A listing ships **at least four** phone screenshots (`storefront-build` refuses
   fewer): two cannot show what the app is, how it works, that it works, and who is
   in control — and the control frame is the one that went missing when three shipped.
   What the listing SAYS is decided before anything is drawn, in
   `docs/storefront-direction.json` (concept, per-frame argument, palette emphasis, evidence).
   Publishing a listing is a **browser** step, not an API call: the Play API uploads and
   commits an edit, but permissions ("Manage store presence") and **Managed publishing** are
   console-only, so `store-publish` ends in Play Console through `browser-control` and nothing
   is called live from an API answer.

5. **Storefront Brand & Marketing Posters (Rule 5):**
   - App Icon (512x512): Created by Stitch MCP (`storefront-brand --stitch-prompt` → adopt).
   - Feature Graphic (1024x500): Painted by Gemini from the hero render (`storefront-brand --generate`).
   - Phone Screenshots (minimum 4): Rendered from real app composables via `storefront-scenes` → framed by Gemini posters (`storefront-build`).
   - Zero procedural HTML mocks or cross-app template leakage. Critic score ≥ 90/100 [Grade A].

NEXT: `devteam storefront-direction <project>` before anything is drawn; `--plan`
shows the path a build would take without painting.
