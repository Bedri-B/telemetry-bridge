"""Shared plumbing for the Kunos sims (Assetto Corsa and ACC).

Both sims expose three memory-mapped files — ``acpmf_physics``,
``acpmf_graphics``, ``acpmf_static`` — with *different* struct layouts per
sim (ACC extends and repurposes AC's). Subclasses supply the ctypes structs
and the frame mapping; this base class handles:

* opening/closing the MMFs (``Local\\`` namespace, external process)
* tear-resistant struct snapshots (packetId double-check)
* new-data detection: physics ``packetId`` advance OR graphics status change
  (the physics counter freezes while paused, but we must still surface the
  pause transition)
* liveness: the graphics page updates every rendered frame even when paused;
  if it stops changing for ``stale_timeout_s`` the sim is gone -> AdapterError

Notes on ``session_time``: the Kunos pages expose time *remaining*
(``sessionTimeLeft``), not elapsed, so adapters report
``-sessionTimeLeft/1000`` — a value that increases monotonically during a
session and rewinds when the session restarts, which is exactly the property
the SessionTracker needs for reset detection. Treat it as a session clock,
not a wall-referenced timestamp.
"""

from __future__ import annotations

import ctypes
import mmap
import time

from ..frames import SimState, TelemetryFrame
from .base import AdapterError, SimAdapter

# AC_STATUS / ACC_STATUS (identical values in both sims)
STATUS_OFF, STATUS_REPLAY, STATUS_LIVE, STATUS_PAUSE = 0, 1, 2, 3

_STATE_MAP = {
    STATUS_OFF: SimState.MENU,       # sim process alive but no live session
    STATUS_REPLAY: SimState.REPLAY,
    STATUS_LIVE: SimState.LIVE,
    STATUS_PAUSE: SimState.PAUSED,
}


def read_struct(mm: mmap.mmap, struct_type: type[ctypes.Structure]):
    """Snapshot a struct from shared memory, retrying on mid-write tears."""
    size = ctypes.sizeof(struct_type)
    snap = struct_type.from_buffer_copy(mm[:size])
    if hasattr(snap, "packetId"):
        for _ in range(3):
            check = struct_type.from_buffer_copy(mm[:size])
            if check.packetId == snap.packetId:
                break
            snap = check
    return snap


class KunosAdapterBase(SimAdapter):
    physics_struct: type[ctypes.Structure]
    graphics_struct: type[ctypes.Structure]
    static_struct: type[ctypes.Structure]
    session_type_names: dict[int, str] = {}
    stale_timeout_s: float = 3.0

    def __init__(self) -> None:
        self._maps: dict[str, mmap.mmap] = {}
        self._last_packet: int | None = None
        self._last_status: int | None = None
        self._last_gfx_packet: int | None = None
        self._last_gfx_change: float = 0.0

    # ------------------------------------------------------------------

    def connect(self) -> None:
        try:
            self._maps = {
                "physics": self._open("acpmf_physics", self.physics_struct),
                "graphics": self._open("acpmf_graphics", self.graphics_struct),
                "static": self._open("acpmf_static", self.static_struct),
            }
        except (OSError, ValueError) as exc:
            self.close()
            raise AdapterError(f"cannot map shared memory: {exc}") from exc

        phys = read_struct(self._maps["physics"], self.physics_struct)
        gfx = read_struct(self._maps["graphics"], self.graphics_struct)
        if phys.packetId == 0 and gfx.status == STATUS_OFF and gfx.packetId == 0:
            # Empty pages: we created the mapping ourselves, sim is not up
            # (or is sitting in the main menu since boot). Retry later.
            self.close()
            raise AdapterError(f"{self.name}: no live session (shared memory empty)")

        self._last_packet = None
        self._last_status = None
        self._last_gfx_packet = None
        self._last_gfx_change = time.monotonic()

    @staticmethod
    def _open(tag: str, struct_type: type[ctypes.Structure]) -> mmap.mmap:
        # Opens the sim's named mapping, or creates a zeroed one if absent
        # (detected in connect() via packetId/status).
        return mmap.mmap(-1, ctypes.sizeof(struct_type), f"Local\\{tag}")

    def close(self) -> None:
        for mm in self._maps.values():
            try:
                mm.close()
            except (BufferError, ValueError):
                pass
        self._maps = {}

    # ------------------------------------------------------------------

    def poll(self, timeout: float) -> TelemetryFrame | None:
        if not self._maps:
            raise AdapterError(f"{self.name}: not connected")
        deadline = time.monotonic() + timeout
        while True:
            gfx = read_struct(self._maps["graphics"], self.graphics_struct)
            now = time.monotonic()

            if gfx.packetId != self._last_gfx_packet:
                self._last_gfx_packet = gfx.packetId
                self._last_gfx_change = now
            elif now - self._last_gfx_change > self.stale_timeout_s:
                raise AdapterError(
                    f"{self.name}: shared memory stale for "
                    f"{self.stale_timeout_s:.0f}s (sim closed?)"
                )

            phys = read_struct(self._maps["physics"], self.physics_struct)
            if phys.packetId != self._last_packet or gfx.status != self._last_status:
                self._last_packet = phys.packetId
                self._last_status = gfx.status
                static = read_struct(self._maps["static"], self.static_struct)
                return self._map_frame(phys, gfx, static)

            if now >= deadline:
                return None
            time.sleep(0.001)

    # ------------------------------------------------------------------

    def _map_frame(self, phys, gfx, static) -> TelemetryFrame:
        """Common-prefix mapping; subclasses extend via _extend_frame."""
        frame = TelemetryFrame(
            sim=self.name,
            tick=phys.packetId,
            state=_STATE_MAP.get(gfx.status, SimState.MENU),
            session_time=-gfx.sessionTimeLeft / 1000.0,
            session_type=self.session_type_names.get(gfx.session, "unknown"),
            lap=gfx.completedLaps,
            lap_dist_pct=max(0.0, min(1.0, gfx.normalizedCarPosition)),
            current_lap_ms=max(0, gfx.iCurrentTime),
            last_lap_ms=max(0, gfx.iLastTime),
            best_lap_ms=self._sane_ms(gfx.iBestTime),
            throttle=phys.gas,
            brake=phys.brake,
            clutch=phys.clutch,
            steer=phys.steerAngle,
            gear=phys.gear - 1,          # Kunos: 0=R, 1=N, 2=1st -> -1/0/1
            speed_mps=phys.speedKmh / 3.6,
            rpm=float(phys.rpms),
            fuel_l=phys.fuel,
        )
        self._extend_frame(frame, phys, gfx, static)
        return frame

    @staticmethod
    def _sane_ms(value: int) -> int:
        # Kunos sims use INT32_MAX-ish sentinels for "no time set yet".
        return value if 0 < value < 86_400_000 else 0

    def _extend_frame(self, frame: TelemetryFrame, phys, gfx, static) -> None:
        """Sim-specific fields (pit flag, coordinates, extras)."""
