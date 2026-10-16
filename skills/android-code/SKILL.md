---
name: android-code
description: >
  Coding-convention skill for the dev-team's developer subagent on
  Android/Kotlin/Compose projects using event-driven MVVM (one-way data flow)
  and Clean Architecture. Distilled from a real production codebase's
  architecture-review doctrine; goal is code that passes that doctrine on
  first attempt. NOT project-specific: layer conventions that vary per project
  (package names, shared components) live in the project profile.
---

# Android coding conventions (developer role)

Read this BEFORE writing code. Where your task spec and this skill conflict on
SCOPE, the task spec wins; on STYLE, this skill wins; on anything touching
the project profile's frozen paths, the profile wins.

## 1. Layers (Clean Architecture)

- **UI layer** (`*Screen`, `*Sheet`, composables): rendering + wiring only.
- **State layer** (ViewModel / BaseViewModel + EventHandlers): state derivation, orchestration, decisions.
- **Data layer** (Repositories, sources): ALL framework/platform access lives here — `PackageManager`, `UsageStatsManager`, `DataStore`, billing clients. Never in composables, never in EventHandlers, never ViewModels calling framework managers directly for data.
- Repositories expose data only — no UI logic, no ViewModels' business decisions. ViewModels depend on repository/manager INTERFACES, never concrete classes (Hilt injects concretions).

## 2. Event-driven ViewModels (the MVI core)

Every stateful ViewModel follows this shape (adapt the base names to the project's own base class — check an existing sibling ViewModel first and mirror its exact form):

```
sealed class <Screen>Event : Event()          // user intents as sealed types
data class <Screen>UIData(... ) : UIData()    // one object holding ALL renderable state

@HiltViewModel class <Screen>ViewModel @Inject constructor(
    ...injected interfaces...
) : BaseViewModel<<Screen>Event, <Screen>UIData>() {
    override val handlers = listOf(OneHandler(...), OtherHandler(...))
    init { onEvent(SomeInitialEvent) }
}
```

- User actions = Event subtypes. NO public `fun doThing()` methods on ViewModels.
- Business logic lives in `EventHandler` implementations (one concern each), whose `handle()` returns `Flow<EventResult>` emitting `StateChange`/`ExecuteAction` — no direct side-effect emissions.
- `canHandle()` matches exactly ONE event type (`is <Concrete>Event`).
- The ViewModel exposes ONLY `state: StateFlow<State<UIData>>` and `actions: SharedFlow<Action>` (inherited). No extra `MutableStateFlow`/`SharedFlow` fields, no `lateinit var` state.
- **Legacy waiver:** if the file you are EDITING predates this pattern (plain fun methods, direct StateFlows), DO NOT retrofit it — extend its existing idiom. Doctrine governs NEW files only. Refactoring an old ViewModel counts as scope creep: note it, don't do it.

## 3. Compose rules (non-negotiable)

- Composables are dumb: no `if/when` on domain logic, no business decisions, no `viewModelScope.launch`, no `Context.getSystemService()`, no framework managers.
- `LaunchedEffect` is for UI effects and navigation only — never data loading or one-time analytics setup (that's VM/handler work).
- State comes down via `collectAsStateWithLifecycle()`; intents go up via event-lambda parameters (state down, events up — always).
- State hoisting: leaf composables take values + lambdas, not ViewModels. The screen root owns the ViewModel.
- Use the project's design tokens (color/spacing/duration objects provided by its theme) — no raw hex colors or magic dp/timestamps where tokens exist.
- Strings: EVERY user-visible string through resources with a decent name; never literals in composables. New strings must be added to ALL locale files the project declares (profile lists these).

## 4. SOLID quick law

- **One reason to change:** repository = data; ViewModel = state; composable = UI; handler = one event type.
- **Open/Closed:** new behavior = new Event subtypes + new EventHandlers, NOT `if/else` inside existing handlers or ViewModels.
- **Dependency inversion:** inject interfaces; never construct dependencies manually.
- **Interface segregation:** repositories expose only what their caller needs.

## 5. Hilt

- ViewModels: `@HiltViewModel` + `@Inject constructor(: interfaces)`.
- Singletons: `@Singleton class X @Inject constructor(...)`.
- No manual instantiation of things that are injectable. Don't create modules unless a binding genuinely needs one; prefer the existing module.
- Composables get ViewModels via `hiltViewModel()`; never pass ViewModels down the tree.

## 6. Analytics discipline

Every user-visible action gets a typed event: a `sealed class AnalyticsEvent` entry + a `when` branch in the tracker implementation. Both halves or neither — no orphans. Awareness events too (`*Shown`), dismissed events, etc. Keep the event if the tracker needs a param (e.g. entry `source`).

## 7. DataStore & settings

DataStore access is rep-only: a single app-scoped DataStore is wrapped by repository classes; no new DataStore instances, no direct `context.dataStore` outside a repository.

## 8. Packaging & placement

Follow the project's feature-package map (from the profile's package rules when supplied). Generic law: shared utilities in the shared package; feature files in that feature's package; NO direct imports from one feature package into another — go through shared types.

## 9. File naming

`XxxScreen`, `XxxSheet`, `XxxDialog` (UI), `XxxViewModel` + `XxxUIData`, `XxxEvent`, `XxxEventHandler`, `XxxRepository` (+ interface), `XxxEvent` in analytics. Keep consistent with sibling files.
