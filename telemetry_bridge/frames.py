"""Normalized telemetry frame schema shared by all adapters and outputs."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class SimState(str, Enum):
    """Coarse simulator state, normalized across sims."""

    OFF = "off"          # sim not running / shared memory absent
    MENU = "menu"        # sim running but no live session
    LIVE = "live"        # on track, physics active
    PAUSED = "paused"    # session paused
    REPLAY = "replay"    # replay playback (not logged as live data)


@dataclass(slots=True)
class TelemetryFrame:
    """One normalized telemetry sample.

    Adapters fill in whatever the sim provides; missing values stay at their
    defaults. ``tick`` MUST be a monotonically increasing per-sample counter
    from the sim (packetId / SessionTick) — it drives frame deduplication.
    """

    sim: str = "unknown"
    tick: int = -1
    state: SimState = SimState.OFF

    # Timing
    wall_time: float = field(default_factory=time.time)
    session_time: float = 0.0          # seconds since session start (sim clock)
    session_type: str = ""             # practice / qualify / race / hotlap ...

    # Lap
    lap: int = 0                       # completed laps (current lap index)
    lap_dist_pct: float = 0.0          # 0..1 position around the lap
    current_lap_ms: int = 0
    last_lap_ms: int = 0
    best_lap_ms: int = 0

    # Driver inputs
    throttle: float = 0.0              # 0..1
    brake: float = 0.0                 # 0..1
    clutch: float = 0.0                # 0..1
    steer: float = 0.0                 # -1..1 (or radians, adapter-normalized)
    gear: int = 0                      # -1 reverse, 0 neutral, 1..n

    # Car state
    speed_mps: float = 0.0
    rpm: float = 0.0
    fuel_l: float = 0.0
    in_pit: bool = False

    # World position (when the sim provides it)
    pos_x: float = 0.0
    pos_y: float = 0.0
    pos_z: float = 0.0

    # Free-form extras an adapter wants to pass through (kept small)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


@dataclass(slots=True)
class TaggedFrame:
    """A TelemetryFrame annotated by the SessionTracker."""

    frame: TelemetryFrame
    session_id: str = ""
    lap_id: int = 0            # monotonically increasing lap counter within session
    events: tuple[str, ...] = ()   # e.g. ("session_start", "lap_complete")

    def to_dict(self) -> dict[str, Any]:
        d = self.frame.to_dict()
        d["session_id"] = self.session_id
        d["lap_id"] = self.lap_id
        if self.events:
            d["events"] = list(self.events)
        return d
