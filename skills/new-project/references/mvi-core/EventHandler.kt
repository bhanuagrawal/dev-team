package example.core.event

import kotlinx.coroutines.flow.Flow

/*
 * One business rule. A handler owns exactly ONE event type:
 *  - canHandle() matches a single concrete event,
 *  - handle() returns a Flow of EventResult (StateChange / ExecuteAction).
 * ViewModels stay dumb: they only declare handlers and dispatch.
 *
 * HANDLER ORDERING — emit your StateChange BEFORE you await anything.
 * A handler runs on the view model's scope while the UI keeps dispatching, so a
 * `flow { persist(state); emit(StateChange { ... }) }` is a race: a second tap
 * arriving during the suspend write reads the STALE state and recomputes the
 * same value (the event is silently lost), and two overlapping writes can even
 * land on disk out of order. Compute the next state, emit the StateChange for
 * it, and only then do the I/O — or split the value out first, e.g.
 * `val next = advance(state); emit(StateChange { next }); repository.save(next)`.
 * Pair that with a serialising primitive in the repository (a Mutex around
 * read/write) so overlapping saves cannot reorder. This cost a shipped app two
 * real defects that its compile and unit gates could not see.
 */
interface EventHandler<UI_DATA : UIData, E : Event> {
    fun canHandle(event: E): Boolean
    fun handle(event: E, state: UI_DATA): Flow<EventResult>
}
