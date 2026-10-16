package example.core.event

/*
 * dev-team reference: the event-driven MVI core for greenfield KMP projects.
 * Pure coroutines — no androidx.lifecycle — so it survives the later iOS target.
 * The create-agent copies this into <project>/composeApp/src/commonMain/kotlin/
 * <package>/core/event/ and updates the package line. These are the project's
 * FROZEN files once adopted.
 */

/** Base marker for user-intent events. One sealed class per screen/feature. */
interface Event

/** Base marker for the single renderable state object of a screen. */
interface UIData

/** What an EventHandler hands back as a flow emission. */
interface EventResult

/** Change the visible state of the screen. */
data class StateChange<UIDATA : UIData>(val mutator: (UIDATA) -> UIDATA) : EventResult

/** Ask the platform to do something observable (route, notify, purchase…). */
data class ExecuteAction(val block: () -> Unit) : EventResult
