"""iRacing adapter, built on pyirsdk (github.com/kutu/pyirsdk).

iRacing's live shared memory updates at a fixed 60 Hz. Notes:

* ``freeze_var_buffer_latest()`` pins one triple-buffered tick so all reads
  in a poll come from the same sim frame (it also paces us, blocking on the
  sim's data-valid event).
* ``SessionTick`` is the monotonic update counter -> our dedup tick.
* There is no explicit "paused" variable: when the sim is paused the tick
  stops advancing while the connection stays up. After ``pause_after_s`` of
  stall we emit a PAUSED frame so downstream sees the transition.
* Connect/disconnect follows the canonical pyirsdk pattern:
  ``startup() and is_initialized and is_connected``; ``shutdown()`` on drop.
"""

from __future__ import annotations

import time

from ..frames import SimState, TelemetryFrame
from .base import AdapterError, SimAdapter


class IRacingAdapter(SimAdapter):
    name = "iracing"
    native_rate_hz = 60.0
    pause_after_s = 0.5              # tick stall before we report PAUSED

    def __init__(self) -> None:
        self._ir = None
        self._last_tick: int | None = None
        self._last_advance = 0.0
        self._session_num: int | None = None
        self._session_type = ""

    # ------------------------------------------------------------------

    def connect(self) -> None:
        try:
            import irsdk
        except ImportError as exc:
            raise AdapterError(
                "pyirsdk is not installed — pip install telemetry-bridge[iracing]"
            ) from exc

        ir = irsdk.IRSDK()
        if not (ir.startup() and ir.is_initialized and ir.is_connected):
            ir.shutdown()
            raise AdapterError("iRacing is not running")
        self._ir = ir
        self._last_tick = None
        self._last_advance = time.monotonic()

    def close(self) -> None:
        if self._ir is not None:
            self._ir.shutdown()
            self._ir = None

    # ------------------------------------------------------------------

    def poll(self, timeout: float) -> TelemetryFrame | None:
        ir = self._ir
        if ir is None or not (ir.is_initialized and ir.is_connected):
            raise AdapterError("iRacing disconnected")

        ir.freeze_var_buffer_latest()    # blocks until fresh data (~60 Hz pacing)
        tick = ir["SessionTick"]
        if tick is None:
            time.sleep(min(timeout, 0.05))
            return None

        now = time.monotonic()
        if tick == self._last_tick:
            # No new sim frame. Long stall while connected == paused.
            if now - self._last_advance > self.pause_after_s:
                return self._build_frame(ir, tick, paused=True)
            time.sleep(min(timeout, 0.016))
            return None

        self._last_tick = tick
        self._last_advance = now
        return self._build_frame(ir, tick, paused=False)

    # ------------------------------------------------------------------

    def _build_frame(self, ir, tick: int, paused: bool) -> TelemetryFrame:
        def val(key, default=0):
            v = ir[key]
            return default if v is None else v

        if paused:
            state = SimState.PAUSED
        elif val("IsReplayPlaying", False):
            state = SimState.REPLAY
        elif not val("IsOnTrack", False):
            state = SimState.MENU
        else:
            state = SimState.LIVE

        return TelemetryFrame(
            sim=self.name,
            tick=tick,
            state=state,
            session_time=float(val("SessionTime")),
            session_type=self._get_session_type(ir),
            lap=int(val("Lap")),
            lap_dist_pct=float(val("LapDistPct")),
            current_lap_ms=self._ms(val("LapCurrentLapTime")),
            last_lap_ms=self._ms(val("LapLastLapTime")),
            best_lap_ms=self._ms(val("LapBestLapTime")),
            throttle=float(val("Throttle")),
            brake=float(val("Brake")),
            # iRacing reports clutch engagement (1=engaged/pedal up); flip it
            # so 'clutch' means pedal input like the other sims.
            clutch=1.0 - float(val("Clutch", 1.0)),
            steer=float(val("SteeringWheelAngle")),   # radians
            gear=int(val("Gear")),                     # already -1/0/1..n
            speed_mps=float(val("Speed")),             # already m/s
            rpm=float(val("RPM")),
            fuel_l=float(val("FuelLevel")),
            in_pit=bool(val("OnPitRoad", False)),
            extras={"session_state": int(val("SessionState"))},
        )

    @staticmethod
    def _ms(seconds) -> int:
        try:
            s = float(seconds)
        except (TypeError, ValueError):
            return 0
        return int(s * 1000) if s > 0 else 0

    def _get_session_type(self, ir) -> str:
        """SessionType lives in the session-info YAML; cache per SessionNum."""
        num = ir["SessionNum"]
        if num is None:
            return self._session_type
        if num != self._session_num:
            try:
                sessions = ir["SessionInfo"]["Sessions"]
                self._session_type = str(sessions[num]["SessionType"]).lower()
                self._session_num = num
            except (KeyError, IndexError, TypeError):
                self._session_type = ""
        return self._session_type
