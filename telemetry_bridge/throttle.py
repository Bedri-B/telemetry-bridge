"""Rate limiter for the live stream: sample latest frame at a target rate."""

from __future__ import annotations


class RateGate:
    """Decides, per frame, whether the live stream should emit it.

    Latest-value sampling: the caller always overwrites its "latest" slot;
    the gate only answers "has enough time passed to send again?" based on
    the frame's own timestamps, so it works identically in real time and in
    accelerated tests. Frames carrying events are always allowed through so
    clients never miss lap/session boundaries.
    """

    def __init__(self, rate_hz: float) -> None:
        if rate_hz <= 0:
            raise ValueError("rate_hz must be > 0")
        self.interval = 1.0 / rate_hz
        self._last_sent: float | None = None

    def allow(self, timestamp: float, has_events: bool = False) -> bool:
        if has_events:
            self._last_sent = timestamp
            return True
        if self._last_sent is None:
            self._last_sent = timestamp
            return True
        if timestamp - self._last_sent >= self.interval:
            # Credit by whole intervals rather than snapping to the frame's
            # timestamp — otherwise quantization against the input rate
            # (e.g. 120 Hz frames through a 60 Hz gate) undershoots the
            # target rate by skipping a frame per beat period.
            self._last_sent += self.interval * ((timestamp - self._last_sent) // self.interval)
            return True
        return False

    def reset(self) -> None:
        self._last_sent = None
