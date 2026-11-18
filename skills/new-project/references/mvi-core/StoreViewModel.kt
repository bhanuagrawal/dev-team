package com.example.app.core.store

import com.example.app.core.event.BaseViewModel
import com.example.app.core.event.Event
import com.example.app.core.event.EventHandler
import com.example.app.core.event.UIData
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn

/**
 * The phone's adapter around a headless [StateStore] — and nothing else.
 *
 * `BaseViewModel` exists to survive Android lifecycle events. It must not be
 * where a product rule lives, because the same rules run in a browser tab. This
 * class is the whole lifecycle bridge: it constructs the store for the caller's
 * scope and re-exposes the exact surface existing Compose screens already use
 * ([state] as the `BaseViewModel.State` wrapper, [onEvent], [actions]).
 *
 * A feature migrates by changing its superclass and passing its handlers:
 *
 * ```
 * class WishesViewModel(scope: CoroutineScope, ...) :
 *     StoreViewModel<WishesUiState, WishesEvent>(
 *         scope = scope,
 *         initial = WishesUiState(),
 *         handlers = listOf(WishesEventHandler(repository)),
 *     )
 * ```
 *
 * No screen changes: the state type is identical. `core/event/BaseViewModel`
 * stays untouched for screens that have not moved yet.
 */
open class StoreViewModel<UI_DATA : UIData, E : Event>(
    scope: CoroutineScope,
    initial: UI_DATA,
    handlers: List<EventHandler<UI_DATA, E>>,
) {

    private val store = StateStore(scope, initial, handlers)

    /**
     * `Eagerly`, because a Compose screen may read `.value` on the first frame
     * before a collector attaches; the store already holds the value, so the
     * adapter only wraps it.
     */
    val state: StateFlow<BaseViewModel.State<UI_DATA>> = store.state
        .map { BaseViewModel.State(it) }
        .stateIn(scope, SharingStarted.Eagerly, BaseViewModel.State(initial))

    val actions: Flow<() -> Unit> = store.actions

    /** The only way in: one intent, one handler. */
    fun onEvent(event: E) = store.dispatch(event)

    /** The headless store itself, for a host that wants the raw state. */
    fun headless(): StateStore<UI_DATA, E> = store
}
