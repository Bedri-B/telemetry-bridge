"""Mock simulator adapter: synthetic telemetry for testing without a sim.

Generates a physically plausible lap (speed profile with corners and
straights, gear/RPM derived from speed) at a configurable rate, and can be
scripted to reproduce edge cases:

* ``pause_at``/``pause_for``   — session pause window (tick and clock freeze,
  like a real sim's shared memory during pause)
* ``reset_at``                 — session restart (clock rewinds, laps reset)
* ``duplicate_every``          — re-deliver every Nth frame with the same tick
* ``disconnect_at``            — simulate the sim process exiting
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from ..frames import SimState, TelemetryFrame
from .base import AdapterError, SimAdapter


@dataclass
class MockScenario:
    rate_hz: float = 60.0
    lap_seconds: float = 45.0        # synthetic lap length in time
    session_type: str = "practice"
    pause_at: float | None = None    # session-seconds (one-shot)
    pause_for: float = 5.0           # seconds worth of paused frames
    reset_at: float | None = None    # session-seconds (one-shot)
    duplicate_every: int = 0         # 0 = never
    disconnect_at: float | None = None
    realtime: bool = True            # False = generate as fast as possible (tests)


class MockAdapter(SimAdapter):
    name = "mock"

    def __init__(self, scenario: MockScenario | None = None) -> None:
        self.scenario = scenario or MockScenario()
        self.native_rate_hz = self.scenario.rate_hz
        self._connected = False
        self._tick = 0
        self._session_time = 0.0
        self._did_reset = False
        self._did_pause = False
        self._pause_frames_left = 0
        self._repeat_frames_left = 0
        self._next_emit = 0.0

    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._connected = True
        self._tick = 0
        self._session_time = 0.0
        self._did_reset = False
        self._did_pause = False
        self._pause_frames_left = 0
        self._repeat_frames_left = 0
        self._next_emit = time.perf_counter()

    def close(self) -> None:
        self._connected = False

    def poll(self, timeout: float) -> TelemetryFrame | None:
        if not self._connected:
            raise AdapterError("mock adapter not connected")
        sc = self.scenario

        if sc.realtime:
            now = time.perf_counter()
            if now < self._next_emit:
                wait = self._next_emit - now
                if wait > timeout:
                    time.sleep(timeout)
                    return None
                time.sleep(wait)
            self._next_emit += 1.0 / sc.rate_hz

        if sc.disconnect_at is not None and self._session_time >= sc.disconnect_at:
            self._connected = False
            raise AdapterError("mock sim exited")

        if (
            sc.reset_at is not None
            and not self._did_reset
            and self._session_time >= sc.reset_at
        ):
            self._did_reset = True
            self._session_time = 0.0

        if (
            sc.pause_at is not None
            and not self._did_pause
            and self._session_time >= sc.pause_at
        ):
            self._did_pause = True
            self._pause_frames_left = max(1, int(sc.pause_for * sc.rate_hz))

        # Paused: sim keeps serving frames but tick and session clock freeze.
        if self._pause_frames_left > 0:
            self._pause_frames_left -= 1
            return self._make_frame(paused=True)

        # Duplicate delivery: same tick handed out twice, clock frozen.
        if self._repeat_frames_left > 0:
            self._repeat_frames_left -= 1
            return self._make_frame(paused=False)

        self._tick += 1
        self._session_time += 1.0 / sc.rate_hz
        if sc.duplicate_every > 0 and self._tick % sc.duplicate_every == 0:
            self._repeat_frames_left = 1
        return self._make_frame(paused=False)

    # ------------------------------------------------------------------

    def _make_frame(self, paused: bool) -> TelemetryFrame:
        sc = self.scenario
        t = self._session_time
        lap = int(t // sc.lap_seconds)
        lap_pct = (t % sc.lap_seconds) / sc.lap_seconds

        # Speed profile: base 40 m/s with two "corners" per lap dipping to ~18.
        phase = lap_pct * 2 * math.pi
        speed = 40.0 - 22.0 * max(0.0, math.sin(2 * phase)) ** 2
        gear = max(1, min(6, int(speed // 8) + 1))
        rpm = 2000 + (speed % 8) / 8 * 4500
        throttle = 1.0 if speed > 35 else max(0.0, (speed - 15) / 25)
        brake = 0.0 if speed > 30 else min(1.0, (30 - speed) / 15)

        return TelemetryFrame(
            sim=self.name,
            tick=self._tick,
            state=SimState.PAUSED if paused else SimState.LIVE,
            session_time=t,
            session_type=sc.session_type,
            lap=lap,
            lap_dist_pct=lap_pct,
            current_lap_ms=int((t % sc.lap_seconds) * 1000),
            last_lap_ms=int(sc.lap_seconds * 1000) if lap > 0 else 0,
            best_lap_ms=int(sc.lap_seconds * 1000) if lap > 0 else 0,
            throttle=throttle,
            brake=brake,
            steer=0.6 * math.sin(2 * phase),
            gear=gear,
            speed_mps=speed,
            rpm=rpm,
            fuel_l=max(0.0, 50.0 - 0.02 * t),
        )
