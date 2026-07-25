"""Unit tests for the session/lap state machine."""

from telemetry_bridge.frames import SimState, TelemetryFrame
from telemetry_bridge.session import SessionTracker, SessionTrackerConfig


def frame(tick, t, lap=0, state=SimState.LIVE, sim="mock"):
    return TelemetryFrame(sim=sim, tick=tick, state=state, session_time=t, lap=lap)


def test_first_live_frame_starts_session():
    tracker = SessionTracker()
    tagged = tracker.process(frame(1, 0.0))
    assert tagged is not None
    assert "session_start" in tagged.events
    assert tagged.session_id.startswith("mock-")


def test_duplicate_ticks_dropped():
    tracker = SessionTracker()
    assert tracker.process(frame(1, 0.00)) is not None
    assert tracker.process(frame(2, 0.02)) is not None
    assert tracker.process(frame(2, 0.02)) is None          # duplicate
    assert tracker.process(frame(3, 0.04)) is not None


def test_lap_completion_event_and_lap_id():
    tracker = SessionTracker()
    tracker.process(frame(1, 10.0, lap=0))
    tagged = tracker.process(frame(2, 45.0, lap=1))
    assert "lap_complete" in tagged.events
    assert tagged.lap_id == 1
    # skipped lap counts still keep lap_id aligned with the sim
    tagged = tracker.process(frame(3, 135.0, lap=3))
    assert tagged.lap_id == 3


def test_session_reset_on_clock_rewind():
    tracker = SessionTracker()
    first = tracker.process(frame(1, 100.0, lap=2))
    tagged = tracker.process(frame(2, 0.5, lap=0))           # restart
    assert "session_end" in tagged.events
    assert "session_start" in tagged.events
    assert tagged.session_id != first.session_id
    assert tagged.lap_id == 0


def test_small_clock_jitter_is_not_a_reset():
    tracker = SessionTracker()
    first = tracker.process(frame(1, 10.0))
    tagged = tracker.process(frame(2, 9.5))                  # 0.5s jitter
    assert tagged.session_id == first.session_id
    assert "session_start" not in tagged.events


def test_lap_counter_rewind_is_a_reset():
    tracker = SessionTracker()
    tracker.process(frame(1, 50.0, lap=3))
    tagged = tracker.process(frame(2, 50.1, lap=0))
    assert "session_start" in tagged.events


def test_pause_emits_transition_then_goes_quiet():
    tracker = SessionTracker()
    tracker.process(frame(1, 1.0))
    paused = tracker.process(frame(1, 1.0, state=SimState.PAUSED))  # tick frozen
    assert paused is not None and "paused" in paused.events
    # further paused frames (frozen tick) are dropped
    assert tracker.process(frame(1, 1.0, state=SimState.PAUSED)) is None
    resumed = tracker.process(frame(2, 1.02))
    assert "resumed" in resumed.events


def test_paused_frames_logged_when_configured():
    tracker = SessionTracker(SessionTrackerConfig(log_paused=True))
    tracker.process(frame(1, 1.0))
    assert tracker.process(frame(1, 1.0, state=SimState.PAUSED)) is not None


def test_menu_and_off_frames_dropped_and_close_session():
    tracker = SessionTracker()
    a = tracker.process(frame(1, 5.0))
    assert tracker.process(frame(2, 5.0, state=SimState.MENU)) is None
    b = tracker.process(frame(1, 0.0))                       # back on track
    assert "session_start" in b.events
    assert b.session_id != a.session_id


def test_replay_dropped_by_default():
    tracker = SessionTracker()
    tracker.process(frame(1, 1.0))
    assert tracker.process(frame(2, 1.5, state=SimState.REPLAY)) is None


def test_disconnect_starts_new_session_on_reconnect():
    tracker = SessionTracker()
    a = tracker.process(frame(1, 5.0))
    tracker.sim_disconnected()
    b = tracker.process(frame(1, 5.02))                      # same tick/clock
    assert b is not None and "session_start" in b.events
    assert b.session_id != a.session_id
