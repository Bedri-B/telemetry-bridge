import pytest

from telemetry_bridge.adapters.base import AdapterError
from telemetry_bridge.adapters.mock import MockAdapter, MockScenario
from telemetry_bridge.frames import SimState


def make(scenario_kwargs=None):
    sc = MockScenario(realtime=False, **(scenario_kwargs or {}))
    adapter = MockAdapter(sc)
    adapter.connect()
    return adapter


def drain(adapter, n):
    return [adapter.poll(0.1) for _ in range(n)]


def test_ticks_monotonic_and_plausible_values():
    adapter = make()
    frames = drain(adapter, 300)
    ticks = [f.tick for f in frames]
    assert ticks == sorted(ticks)
    assert len(set(ticks)) == len(ticks)
    for f in frames:
        assert 0 <= f.throttle <= 1
        assert 0 <= f.brake <= 1
        assert 0 < f.speed_mps < 100
        assert 1 <= f.gear <= 6


def test_lap_advances():
    adapter = make({"lap_seconds": 1.0, "rate_hz": 60})
    frames = drain(adapter, 130)          # ~2.16s -> at least 2 laps
    assert frames[-1].lap >= 2


def test_duplicate_scenario_repeats_ticks():
    adapter = make({"duplicate_every": 10})
    ticks = [f.tick for f in drain(adapter, 50)]
    dupes = sum(1 for a, b in zip(ticks, ticks[1:]) if a == b)
    assert dupes >= 4


def test_pause_scenario_freezes_tick_and_clock():
    adapter = make({"pause_at": 0.5, "pause_for": 0.2, "rate_hz": 60})
    frames = drain(adapter, 120)
    paused = [f for f in frames if f.state == SimState.PAUSED]
    assert paused, "expected paused frames"
    assert len({f.tick for f in paused}) == 1
    assert len({f.session_time for f in paused}) == 1
    # resumes afterwards
    assert frames[-1].state == SimState.LIVE


def test_reset_scenario_rewinds_clock():
    adapter = make({"reset_at": 1.0, "rate_hz": 60, "lap_seconds": 0.5})
    frames = drain(adapter, 120)
    times = [f.session_time for f in frames]
    rewinds = sum(1 for a, b in zip(times, times[1:]) if b < a - 0.5)
    assert rewinds == 1


def test_disconnect_scenario_raises():
    adapter = make({"disconnect_at": 0.5, "rate_hz": 60})
    with pytest.raises(AdapterError):
        drain(adapter, 60)
