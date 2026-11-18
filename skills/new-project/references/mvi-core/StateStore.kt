package com.example.app.core.store

// Law: the store renders what the server sent. It never invents an id, a count
// or an action target. Cite your product's own clause where one exists.

import com.example.app.core.event.Event
import com.example.app.core.event.EventHandler
import com.example.app.core.event.ExecuteAction
import com.example.app.core.event.StateChange
import com.example.app.core.event.UIData
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * THE HEADLESS STORE — one screen's whole brain, with no platform in it.
 *
 * `ViewModel` is an Android lifecycle construct: an Activity creates it, a
 * configuration change keeps it, a back stack clears it. None of that is a
 * product rule, and none of it exists in a browser tab — so the rules live here.
 *
 * ```
 * Event ──dispatch──▶ EventHandler ──StateChange──▶ UIData ──state──▶ renderer
 * ```
 *
 * A feature that ships on Android AND the web must put every rule in this
 * store, because the JS client implements the same contract (see the team's
 * `js-client` skill). `BaseViewModel` (the other file in this directory) stays
 * available for screens already written against it, but a new screen starts
 * with `StoreViewModel` + this store, never with handlers inside a ViewModel.
 *
 * The store is a class, not a framework: it takes a scope because it has no
 * lifecycle of its own. The phone passes the Activity's scope; a browser host
 * passes the page's.
 */
class StateStore<UI_DATA : UIData, E : Event>(
    private val scope: CoroutineScope,
    initial: UI_DATA,
    private val handlers: List<EventHandler<UI_DATA, E>>,
) {

    private val _state = MutableStateFlow(initial)

    /** The one renderable truth of this surface. */
    val state: StateFlow<UI_DATA> = _state.asStateFlow()

    private val _actions = MutableSharedFlow<() -> Unit>(extraBufferCapacity = 8)

    /** Platform work a handler requested (route, notify, finish). */
    val actions: Flow<() -> Unit> = _actions.asSharedFlow()

    /**
     * Observers for hosts that are not coroutine collectors.
     *
     * Reassignment, never in-place mutation: `subscribe` may be called from the
     * render loop while the store is reducing. The list being notified is
     * always a value, never one being edited.
     */
    private var observers: List<(UI_DATA) -> Unit> = emptyList()

    fun currentState(): UI_DATA = _state.value

    /**
     * Run one intent. An unknown intent fails loudly — a store that silently
     * ignores an event is how a screen looks broken with no error anywhere.
     */
    fun dispatch(event: E) {
        val handler = handlers.firstOrNull { it.canHandle(event) }
            ?: error("no handler for ${event::class.simpleName}")
        scope.launch {
            handler.handle(event, _state.value).collect { result ->
                when (result) {
                    // `is StateChange<UI_DATA>` does not compile on Kotlin 2.x
                    // ("cannot check for instance of erased type").
                    is StateChange<*> -> {
                        @Suppress("UNCHECKED_CAST")
                        reduce(result as StateChange<UI_DATA>)
                    }
                    is ExecuteAction -> _actions.tryEmit(result.block)
                }
            }
        }
    }

    /**
     * State down, events up — the JavaScript/React seam.
     *
     * The observer is called once with the current state, then on every
     * [StateChange]. Returns the unsubscribe closure; calling it twice is a
     * no-op.
     */
    fun subscribe(observer: (UI_DATA) -> Unit): () -> Unit {
        observers = observers + observer
        observer(_state.value)
        return {
            observers = observers - observer
        }
    }

    private fun reduce(change: StateChange<UI_DATA>) {
        val next = change.mutator(_state.value)
        _state.value = next
        observers.forEach { it(next) }
    }
}
