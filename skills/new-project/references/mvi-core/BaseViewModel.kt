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

/*
 * Event-driven MVI core (pure coroutines).
 * Subclasses declare `handlers` and initial onEvent in init ONLY;
 * UI_DATA changes happen exclusively via EventHandler StateChange;
 * UI observes `state` and calls onEvent(); nothing else is exposed.
 */
abstract class BaseViewModel<UI_DATA : UIData, E : Event>(
    private val scope: CoroutineScope,
    initial: UI_DATA,
) {
    data class State<UI_DATA : UIData>(val ui: UI_DATA)

    private val _state = MutableStateFlow(State(initial))
    val state: StateFlow<State<UI_DATA>> = _state.asStateFlow()

    private val _actions = MutableSharedFlow<() -> Unit>(extraBufferCapacity = 8)
    val actions: Flow<() -> Unit> = _actions.asSharedFlow()

    protected abstract val handlers: List<EventHandler<UI_DATA, E>>

    fun onEvent(event: E) {
        val handler = handlers.firstOrNull { it.canHandle(event) }
            ?: throw IllegalStateException("no handler for ${event::class.simpleName}")
        scope.launch {
            handler.handle(event, _state.value.ui).collect { res ->
                when (res) {
                    is StateChange<UI_DATA> -> _state.value = State(res.mutator(_state.value.ui))
                    is ExecuteAction -> _actions.tryEmit(res.block)
                }
            }
        }
    }
}
