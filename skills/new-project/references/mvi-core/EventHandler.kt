package example.core.event

import kotlinx.coroutines.flow.Flow

/*
 * One business rule. A handler owns exactly ONE event type:
 *  - canHandle() matches a single concrete event,
 *  - handle() returns a Flow of EventResult (StateChange / ExecuteAction).
 * ViewModels stay dumb: they only declare handlers and dispatch.
 */
interface EventHandler<UI_DATA : UIData, E : Event> {
    fun canHandle(event: E): Boolean
    fun handle(event: E, state: UI_DATA): Flow<EventResult>
}
