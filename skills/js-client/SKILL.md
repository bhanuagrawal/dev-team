---
name: js-client
description: >
  Coding-convention and architecture skill for dev-team agents building a JavaScript /
  Web client for a project. Distilled from production multiplatform practice: headless
  StateStore, zero Android-isms (no ViewModels), web platform seams, and Stitch desktop UI.
---

# JS / Web Client Architecture & Conventions

Read this BEFORE building or extending a web client for any dev-team app.

## 1. Core Doctrine: The Headless StateStore (No ViewModels)

`ViewModel` is an Android Jetpack concept built to survive Android Activity recreation. It has no place in a web client.

Every cross-platform state machine in dev-team follows this universal contract:
```
Event (intent) ──▶ EventHandler (business logic & rules) ──▶ StateChange ──▶ StateStore (single state tree)
```

1. **Events** are sealed classes / union types representing user actions. No direct `doThing()` calls.
2. **EventHandlers** contain all validation and business decisions. One handler per event.
3. **StateStore** holds the immutable state and exposes:
   - `state: StateFlow<State<UIData>>` (for Kotlin/Android consumers)
   - `dispatch(event: Event)` (universal dispatch)
   - `subscribe((state) => void): () => void` (for JavaScript / React consumers)

## 2. Platform Seams

Mobile and Web have different hardware and OS primitives. Never leak Android classes into common logic. Use platform interfaces with dedicated implementations:

| Capability | Android Implementation | Web (JS) Implementation |
| :--- | :--- | :--- |
| **Local Cache** | Room 3 SQLite | `localStorage` or `IndexedDB` (idb-keyval) |
| **Location** | `FusedLocationProvider` (GPS) | `navigator.geolocation` with fallback to `KNOWN_CITIES` selector |
| **Scanner / OCR** | CameraX + ML Kit | File input / WebRTC + Cloud Run backend vision API / ZXing |
| **Authentication** | Google Credential Manager | Supabase OAuth redirect or popup |
| **Notifications** | FCM (Firebase Cloud Messaging) | Supabase Realtime (active tab) + Web Push / Email alerts |

## 3. UI Layer: Web-Native UX via Stitch

Do NOT build a stretched-out mobile app. Web interfaces have wide screens, persistent URLs, and keyboard/mouse inputs:

1. **Layout Hierarchy:**
   - Sticky Top Header with global search, location pill, and user profile/passport.
   - Multi-column responsive grid (3–4 columns) for catalogs and shelves.
   - Side-by-side split view for trade/offer composer (You Give $\leftrightarrow$ You Take).
   - Centred modals / dialogs with backdrop blur instead of mobile bottom sheets.
2. **Stitch Workflow:**
   - Generate screens in Stitch using `deviceType: "DESKTOP"`.
   - Maintain the project's design tokens (fonts, color palette, radius).
   - Export HTML + Tailwind CSS to `docs/stitch/screens/<screen-id>/`.
   - The React UI wraps the exported Stitch HTML directly into component JSX.

## 4. React Integration (The `useStore` Hook)

A web view should be completely "dumb"—it renders state and dispatches events:

```typescript
// hooks/useStore.ts
import { useEffect, useState } from 'react';
import type { StateStore, UIData, Event } from '@app/core';

export function useStore<S extends UIData, E extends Event>(store: StateStore<S, E>) {
  const [state, setState] = useState(() => store.state.value.ui);

  useEffect(() => {
    return store.subscribe((nextState) => {
      setState(nextState.ui);
    });
  }, [store]);

  return {
    state,
    dispatch: (event: E) => store.dispatch(event),
  };
}
```

## 5. Step-by-Step: Adding a JS Client to an Existing App

1. **Verify the Core:** Ensure the app's `commonMain` uses `StateStore` (or `BaseViewModel` extending `StateStore`).
2. **Platform Seams:** Implement web versions of any storage, location, or camera interfaces.
3. **Compile or Bridge:** 
   - Option A: Add `js(IR) { browser(); generateTypeScriptDefinitions() }` to the KMP build.
   - Option B: Use TypeScript client with auto-generated Supabase types (`supabase gen types typescript`).
4. **Design Web Screens:** Use `StitchMCP` to generate desktop layouts (`deviceType: "DESKTOP"`).
5. **Assemble Web App:** Setup Next.js or Vite with Tailwind CSS, bind Stitch HTML to `useStore`, and verify that all product laws (e.g. `PRODUCT.md`) hold.
