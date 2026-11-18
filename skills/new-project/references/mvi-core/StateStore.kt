package example.core.event

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlin.js.JsExport

/*
 * Platform-agnostic Headless State Store (pure coroutines).
 *
 * Replaces the Android-only ViewModel concept for cross-platform apps:
 * - NO Android lifecycle or androidx dependencies.
 * - Dispatches user Event to matching EventHandler.
 * - Maintains single immutable State<UI_DATA>.
 * - Emits StateChange and ExecuteAction.
 * - Provides subscribe() for JavaScript / Web / iOS consumers.
 */
@JsExport
open class StateStore<UI_DATA : UIData, E : Event>(
    private val scope: CoroutineScope,
    initial: UI_DATA,
    protected open val handlers: List<EventHandler<UI_DATA, E>>,
) {
    data class State<UI_DATA : UIData>(val ui: UI_DATA)

    private val _state = MutableStateFlow(State(initial))
    val state: StateFlow<State<UI_DATA>> = _state.asStateFlow()

    private val _actions = MutableSharedFlow<() -> Unit>(extraBufferCapacity = 8)
    val actions: Flow<() -> Unit> = _actions.asSharedFlow()

    fun dispatch(event: E) {
        val handler = handlers.firstOrNull { it.canHandle(event) }
            ?: throw IllegalStateException("no handler for ${event::class.simpleName}")
        scope.launch {
            handler.handle(event, _state.value.ui).collect { res ->
                when (res) {
                    is StateChange<*> -> {
                        @Suppress("UNCHECKED_CAST")
                        val change = res as StateChange<UI_DATA>
                        _state.value = State(change.mutator(_state.value.ui))
                    }
                    is ExecuteAction -> _actions.tryEmit(res.block)
                }
            }
        }
    }

    /**
     * JavaScript / Web subscription bridge.
     * Takes a callback invoked on each state change, returns an unsubscribe function.
     */
    fun subscribe(onStateChanged: (State<UI_DATA>) -> Unit): () -> Unit {
        val job = scope.launch {
            _state.collect { onStateChanged(it) }
        }
        return { job.cancel() }
    }
}
