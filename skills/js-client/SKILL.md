---
name: js-client
description: >
  Delegated skill for adding a web/JavaScript client to a dev-team app: a
  headless StateStore instead of a ViewModel, platform seams for cache,
  location, camera and auth, Stitch DESKTOP screens converted to React, and one
  API door to the app's real backend. Use when the owner asks for "a web
  version", "a browser client", "a JS/React app", "a desktop client", or when
  an app needs a second surface that shares its product rules.
---

# The JS client — same product, second host

You are adding a browser client to an app the team already ships. It is **one
product with two hosts**, not a second app with its own rules. The Android app
remains the reference implementation; the web client reuses its backend, its
design system and its product laws.

Read this before writing a single component. Every section below exists because
a previous attempt did it the other way.

## 0 — Decide the share boundary first

There are three legitimate answers. Pick one and write it in the project's
`docs/web-client.md` before code:

| answer | when | sharing |
|---|---|---|
| **Same repo, React + TS store** (the default this skill documents) | the app has a Kotlin/Compose client and the owner wants a desktop surface | backend, design tokens, laws and state shapes; the store contract is implemented in both languages and tested |
| **Kotlin/JS module of the MVI core** | the team already has a JS toolchain and the rules are large | the actual `.kt` files compile to JS; React imports the generated module |
| **Compose in the browser (wasmJs)** | only when the owner explicitly wants pixel-identical Compose | the entire UI; **not a desktop design** — see §4 |

Do not silently pick wasm because "it compiles the common code". A canvas is not
a DOM: it cannot be read by a screen reader, tested with `getByText`, linked to,
or laid out for 1440 px without a new design. The owner asked for a **desktop**
client in JS/React; that is the default.

## 1 — The headless store (never a ViewModel)

`ViewModel` is an Android lifecycle construct. A browser tab has no Activity to
outlive, so **product rules must not live in it**. The store is the brain; the
ViewModel is a 20-line adapter when the phone needs one.

```
Event ──dispatch──▶ EventHandler ──StateChange──▶ UIData ──subscribe──▶ renderer
```

| piece | Kotlin | TypeScript |
|---|---|---|
| intent | `interface Event` | `interface AppEvent { type: string }` |
| rule | `EventHandler<UI, E>` (one event) | `EventHandler<S, E>` (one event) |
| mutation | `StateChange<UI>(mutator)` | `type StateChange<S> = (s: S) => S` |
| state | `interface UIData` | `interface UIData` (one per screen) |
| store | `StateStore(scope, initial, handlers)` | `StateStore(initial, handlers)` |
| host binding | `StoreViewModel` → Compose | `useStore(store)` → React |

Non-negotiable properties of the store:

- **One handler per event.** `canHandle` matches exactly one intent; an
  unhandled intent **throws** (`No handler for "X"`). A silent no-op is how a
  screen looks broken with no error anywhere.
- **State changes are pure.** A handler yields `StateChange`s; it never mutates
  state directly and never calls a repository from the UI.
- **Yield the change before awaiting I/O** where order matters. Two intents must
  not read the same stale snapshot and recompute the same value.
- **Serialise dispatches in JS.** Chain them on a promise queue so the second
  intent reads the state the first produced.
- **`subscribe(observer)` is the JS seam**: called once immediately with the
  current state, then on every change, returns the unsubscribe closure. React
  binds it with `useSyncExternalStore` — never by copying state into `useState`
  (that creates two sources of truth).

```ts
// web/src/core/StateStore.ts (the shape, abbreviated)
class StateStore<S, E extends AppEvent> {
  getState = (): S => this.current;
  subscribe = (observer: (s: S) => void) => { /* immediate + every change */ };
  dispatch(event: E): Promise<void> { /* serialised; one handler; reduce */ }
}

// web/src/core/useStore.ts
export function useStore<S, E extends AppEvent>(store: StateStore<S, E>) {
  const state = useSyncExternalStore(store.subscribe, store.getState, store.getState);
  const dispatch = useCallback((e: E) => void store.dispatch(e), [store]);
  return [state, dispatch] as const;
}
```

On the phone, install `core/store/StateStore.kt` **and**
`core/store/StoreViewModel.kt` (this skill's sibling template under
`skills/new-project/references/mvi-core/`). Migrate a feature by changing its
superclass to `StoreViewModel` and passing the same handler list; the Compose
screen does not change — `state` stays a `StateFlow<BaseViewModel.State<UI>>`.
Leave `core/event/BaseViewModel.kt` frozen.

**Every law is enforced in a handler**, not in a renderer. If a button is
disabled, a handler must also refuse the event — a second renderer (the web
one) will not have that button's `disabled` attribute.

## 2 — Platform seams (never leak a framework)

Put the difference behind an interface in shared code; each host answers it.
The web answers, proven in BookSwap:

| seam | Android | browser | rule |
|---|---|---|---|
| cache | Room 3 for structured inventory; preferences for small blobs | `localStorage` (small), `IndexedDB` (large) | a cache is never the source of truth; keys are reader-scoped; a failed read is an empty read |
| location | one coarse fix, city list fallback | `navigator.geolocation`, the same city list | return coordinates + a place name; **never compute or show a distance** |
| camera / scan | CameraX + ML Kit on-device | OS file/camera picker → the same vision route | the seam is the photo, not the hardware; base64 + preview |
| auth | Credential Manager / native | standard OAuth browser redirect | read the fragment once, strip it from the address bar, store the token; identity only |
| fonts | bundled TTFs | the same families over a `<link>` | one typographic system |

The location seam must degrade **explicitly**: a denied permission is a plain
sentence plus a real fallback city, not a silent guess and not an empty screen.

```ts
interface LocationSource { current(preferred?: KnownCity): Promise<LocationFix>; }
interface Cache { read(k: string): Promise<string | null>; write(k: string, v: string): Promise<void>; remove(k: string): Promise<void>; clear(): Promise<void>; }
interface ShelfPhotoSource { read(file: File): Promise<{ previewUrl: string; base64: string }>; }
interface Auth { session(): Session | null; signIn(): Promise<void>; signOut(): void; onChange(cb: (s: Session | null) => void): () => void; consumeRedirect(): Promise<Session | null>; }
```

## 3 — One API door

The web client talks **only** to the project's real backend (Cloud Run / the
app's REST service). It never queries the database:

- **No** `supabase.co/rest/v1`, no PostgREST, no direct table reads, even when a
  client key is public.
- **No** hardcoded arrays, fixture JSON, or "demo mode". A screen with no server
  answer says so, with the server's own sentence when there is one.
- Supabase (or the identity provider) is used **for identity only**: the OAuth
  round trip and reading the signed-in user's id from `/auth/v1/user`.
- Cache last real answers; label them ("the last answer this device kept") and
  replace them with the live read. Never seed state from a fixture.

Mirror the phone's DTOs in `web/src/api/types.ts`, and keep one `BookSwapApi`-
style class that owns base URL, auth headers, and error→sentence mapping. Read
the backend's own OpenAPI (`/openapi.json`) before typing a response; the real
field names are the contract (BookSwap's search projection is snake_case, newer
projections are camelCase — render what the server sends).

## 4 — Desktop design via Stitch, then React

**A desktop design is a new design surface.** A stretched phone screen is a
refusal, not a deliverable.

1. Run `devteam design-consult <project> --task <label> --prompt` and answer
   `new_screen` (or `tweak_screen`) — never write UI before the recorded answer.
2. Generate the screens in Stitch with **`deviceType: "DESKTOP"`**, passing the
   project's existing **design system** id, so the palette, type ramp, radii and
   border weights are inherited rather than re-invented. Lock the product laws
   in the prompt (one area; one copy each way; no money; no claimed match).
3. Export the canonical HTML **and** screenshot into
   `docs/stitch/screens/<slug>/` and record each screen in
   `docs/stitch/manifest.json` with `"deviceType": "DESKTOP"`. Commit them.
4. **Convert that HTML directly.** Keep its markup and Tailwind class names —
   the React component should read like the export. Do not "design it again in
   components".

Desktop UX, not mobile UX:

- sticky 72 px top bar: wordmark, one wide search, area pill, trust passport;
- a rail + a 3–4 column grid for catalogues; 1440 px content, 32 px gutters;
- side-by-side workbenches ("What you give" ↔ "What you take") instead of a
  stacked flow;
- centred dialogs with `Esc` and a visible `Esc` keycap, not bottom sheets;
- no dead controls: a sort button that sorts nothing, or a "Load more" with no
  pagination, is a lie. Render only actions the API backs.

**Design tokens are generated, never copied.** Write an exporter from the
Kotlin theme (`Color.kt`, `Type.kt`, `Spacing.kt`) to CSS custom properties, run
it in the build gate, and fail when it is stale (`--check`). A hand-copied hex
is a palette that will drift.

## 5 — Repository layout

```
web/
  package.json            react + vite + typescript + tailwind + vitest
  index.html              fonts, title, OG; <div id="root">
  src/
    config.ts             API_BASE, identity URL, AREA constant (one place)
    api/{client,types}.ts ONE door + the server's own shapes
    core/
      contract.ts         AppEvent / StateChange / EventHandler
      StateStore.ts       headless store
      useStore.ts         React binding (useSyncExternalStore)
      laws.ts             PRODUCT.md clauses as executable refusals
      *.store.ts          one store per surface + its handlers
    platform/             cache, location, auth, shelf-photo
    design/               tokens.css (generated) + base.css
    ui/                   components converted from the Stitch export
    app/stores.ts         composition root: one instance per seam/store
  tests/                  law + store tests (named after the clauses)
```

One store per surface; one composition root; no store constructed inside a
component (that is how two tabs of the app get two sessions).

## 6 — Build gate and the three allowlists

Ship a `scripts/web-check.sh` that runs, in order:

```bash
python3 scripts/export-web-tokens.py --check   # tokens match the Kotlin theme
npm ci && npm test                              # law + store tests
npm run build                                   # tsc --noEmit && vite build
```

`npm run build` must be **zero type errors** (`strict: true`; keep
`noUnusedLocals`). A green build is not "live", though. A browser client is done
only when the page, its modules and both hosts answer a real origin:

1. **Static host** — serve `web/dist` (`base: "./"` works under a sub-path).
2. **Identity allowlist** — the provider's redirect URL must name the exact
   origin; add it in Supabase → Authentication → URL Configuration
   (`http://localhost:5173/**` for dev, the exact hosted page URL otherwise).
   **An unlisted redirect is not an error:** Supabase silently falls back to the
   project's Site URL, so the token lands on a different page (in BookSwap's
   case the GitHub Pages marketing site) and the app never sees it. If sign-in
   "works" but leaves your app, check the allowlist before the code.
3. **API CORS** — the backend's `CORS_ORIGINS` must include the origin. `curl`
   ignores CORS, so it always "passes"; test with a real browser request from
   the page's own origin and read `Access-Control-Allow-Origin`.

Then commit: the app, the screens, the docs page, and the dev-team changes.

## 7 — Pitfalls that cost real time

- **`fetch` loses its `this`.** `this.fetchImpl(...)` where `fetchImpl = fetch`
  throws `TypeError: Illegal invocation` in Chrome, and the request never
  leaves the tab. Store it as an arrow: `(...args) => fetch(...args)`.
- **A canvas is not a page.** Compose/wasm renders pixels: no DOM queries, no
  screen readers, no `getByText`, no desktop layout without a new design.
- **`<canvas>` as a mount point** nests Compose's own canvas in a replaced
  element (0×0 box, blank page, no error) — use a `<div>`. (The wasm build
  learned this the hard way.)
- **The first "live" run is usually CORS**, and it fails only in the browser.
- **A sign-in that lands on the wrong page is the redirect allowlist**, not an
  auth bug: an unlisted `redirect_to` falls back to the Site URL. The token in
  that URL is real — close the tab and add the origin.
- **A cached answer is not a mock** only if it came from the server, is labelled,
  and is replaced by the live read.
- **Duplicate tags are real data.** Dedupe for display, never invent
  categories to make a rail look tidy.
- **Never render a money surface**, even in a disabled state: no price field,
  no currency symbol, no "free". The law is about the product, not the payload.

## 8 — Definition of done

- [ ] Headless `StateStore` exists in shared/common code; the Android build is
      still green; at least one screen runs on it (no rules in a ViewModel).
- [ ] Web has real implementations of the cache, location, camera and auth seams.
- [ ] Stitch generated the desktop screens with `deviceType: "DESKTOP"` and the
      exported HTML + screenshots are committed; React components are converted
      from that HTML.
- [ ] Every read/write goes through the real backend; there is no direct database
      call and no fixture array.
- [ ] The product laws are enforced in event handlers and covered by tests.
- [ ] `npm run build` passes with zero type errors; tokens `--check` passes.
- [ ] The three allowlists are recorded (host, redirect, CORS) and a real origin
      has answered a real request.
- [ ] Both repos are committed and pushed; the report names the refs.
