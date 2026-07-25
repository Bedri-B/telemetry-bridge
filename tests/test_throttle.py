from telemetry_bridge.throttle import RateGate


def test_downsamples_to_target_rate():
    gate = RateGate(60.0)
    # 333 Hz input for one second
    sent = sum(gate.allow(i / 333.0) for i in range(333))
    assert 55 <= sent <= 62


def test_event_frames_always_pass():
    gate = RateGate(1.0)
    assert gate.allow(0.0)
    assert not gate.allow(0.1)
    assert gate.allow(0.2, has_events=True)


def test_reset_allows_immediately():
    gate = RateGate(1.0)
    assert gate.allow(0.0)
    gate.reset()
    assert gate.allow(0.01)


def test_invalid_rate_rejected():
    import pytest

    with pytest.raises(ValueError):
        RateGate(0)
