"""Session/lap state machine.

Pure logic, no I/O — this is the unit-tested core that turns a raw adapter
frame stream into a tagged stream with session ids, lap ids and lifecycle
events, while protecting downstream consumers from sim quirks:

* duplicate frames (same tick delivered twice)  -> dropped
* pauses                                        -> passed through, flagged
* session resets / restarts / sim exits         -> new session id + events
* replay playback                               -> dropped (configurable)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .frames import SimState, TaggedFrame, TelemetryFrame

# A backwards jump in sim session clock larger than this means the session
# was reset/restarted (small negative jitter can occur on some sims).
_SESSION_TIME_REWIND_S = 2.0


def _new_session_id(sim: str) -> str:
    return f"{sim}-{uuid.uuid4().hex[:12]}"


@dataclass
class SessionTrackerConfig:
    log_replay: bool = False       # emit frames while sim is in replay mode
    log_paused: bool = False       # emit frames while paused (still tagged)


class SessionTracker:
    """Feed raw frames in via :meth:`process`; get tagged frames (or None) out."""

    def __init__(self, config: SessionTrackerConfig | None = None) -> None:
        self.config = config or SessionTrackerConfig()
        self.session_id: str = ""
        self.lap_id: int = 0
        self._last_tick: int | None = None
        self._last_session_time: float | None = None
        self._last_lap: int | None = None
        self._last_state: SimState = SimState.OFF
        self._session_open = False

    # ------------------------------------------------------------------

    def process(self, frame: TelemetryFrame) -> TaggedFrame | None:
        """Return a tagged frame, or None if the frame should be dropped."""
        events: list[str] = []

        # --- sim lifecycle ------------------------------------------------
        if frame.state in (SimState.OFF, SimState.MENU):
            if self._session_open:
                self._close_session()
            self._last_state = frame.state
            return None

        if frame.state == SimState.REPLAY and not self.config.log_replay:
            self._last_state = frame.state
            return None

        # --- dedup ---------------------------------------------------------
        # A sim's tick counter freezes while paused, so a state change must
        # never be swallowed as a duplicate — only drop same-tick frames when
        # the coarse state is unchanged too.
        if (
            self._last_tick is not None
            and frame.tick == self._last_tick
            and frame.state == self._last_state
        ):
            return None
        # Tick going backwards (without a session-time rewind) can happen when
        # a sim restarts its counter mid-session; treat as reset below via
        # session_time, but never treat it as a duplicate.

        # --- session boundary detection -------------------------------------
        reset = False
        if not self._session_open:
            reset = True  # first live frame ever, or after sim exit
        elif (
            self._last_session_time is not None
            and frame.session_time < self._last_session_time - _SESSION_TIME_REWIND_S
        ):
            reset = True  # session clock rewound: restart/reset
        elif self._last_lap is not None and frame.lap < self._last_lap:
            reset = True  # lap counter went backwards: new session/stint reset

        if reset:
            if self._session_open:
                events.append("session_end")
            self.session_id = _new_session_id(frame.sim)
            self.lap_id = frame.lap
            self._session_open = True
            events.append("session_start")

        # --- pause handling --------------------------------------------------
        if frame.state == SimState.PAUSED:
            first_pause = self._last_state != SimState.PAUSED
            if first_pause:
                events.append("paused")
            if not self.config.log_paused and "session_start" not in events:
                # Surface the pause transition itself, then go quiet until resume.
                result = (
                    TaggedFrame(frame, self.session_id, self.lap_id, tuple(events))
                    if first_pause
                    else None
                )
                self._remember(frame)
                return result
        elif self._last_state == SimState.PAUSED:
            events.append("resumed")

        # --- lap boundary -----------------------------------------------------
        if not reset and self._last_lap is not None and frame.lap > self._last_lap:
            # A sim can conceivably skip a lap count on missed frames; advance
            # our lap id by the same delta so lap ids stay aligned with the sim.
            self.lap_id += frame.lap - self._last_lap
            events.append("lap_complete")

        self._remember(frame)
        return TaggedFrame(frame, self.session_id, self.lap_id, tuple(events))

    def sim_disconnected(self) -> None:
        """Notify the tracker that the sim went away (adapter disconnect)."""
        self._close_session()

    # ------------------------------------------------------------------

    def _remember(self, frame: TelemetryFrame) -> None:
        self._last_tick = frame.tick
        self._last_session_time = frame.session_time
        self._last_lap = frame.lap
        self._last_state = frame.state

    def _close_session(self) -> None:
        self._session_open = False
        self._last_tick = None
        self._last_session_time = None
        self._last_lap = None
        self._last_state = SimState.OFF
