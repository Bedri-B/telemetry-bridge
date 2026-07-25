"""Assetto Corsa Competizione adapter.

ACC reuses AC's MMF names but extends the layouts (Kunos'
ACCSharedMemoryDocumentationV1.8.12, unchanged since ACC 1.8.12; cross-checked
against rrennoir/pyAccSharedMemory). The physics page is byte-identical to
AC's through ``localVelocity`` and then appends ACC-only fields; the graphics
page diverges after ``normalizedCarPosition`` where ACC inserts multi-car
coordinate arrays. We map each page as a prefix struct covering every field
this bridge consumes — reading a prefix of the page is safe because the MMF
sizes are fixed by the sim.
"""

from __future__ import annotations

import ctypes
from ctypes import c_float, c_int32, c_wchar

from ..frames import TelemetryFrame
from .assetto import ACPhysics
from .kunos_common import KunosAdapterBase

# Physics: AC layout is a byte-exact prefix of ACC's physics page and already
# covers everything we consume (ACC's appended fields are wheel/brake detail).
ACCPhysics = ACPhysics


class ACCGraphics(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        # -- common prefix shared with AC ---------------------------------
        ("packetId", c_int32), ("status", c_int32), ("session", c_int32),
        ("currentTime", c_wchar * 15), ("lastTime", c_wchar * 15), ("bestTime", c_wchar * 15),
        ("split", c_wchar * 15), ("completedLaps", c_int32), ("position", c_int32),
        ("iCurrentTime", c_int32), ("iLastTime", c_int32), ("iBestTime", c_int32),
        ("sessionTimeLeft", c_float), ("distanceTraveled", c_float), ("isInPit", c_int32),
        ("currentSectorIndex", c_int32), ("lastSectorTime", c_int32), ("numberOfLaps", c_int32),
        ("tyreCompound", c_wchar * 33), ("replayTimeMultiplier", c_float),  # unused in ACC
        ("normalizedCarPosition", c_float),
        # -- ACC divergence -------------------------------------------------
        ("activeCars", c_int32),
        ("carCoordinates", c_float * 3 * 60),   # [carIdx][xyz]
        ("carID", c_int32 * 60),
        ("playerCarID", c_int32),
        ("penaltyTime", c_float), ("flag", c_int32), ("penalty", c_int32),
        ("idealLineOn", c_int32), ("isInPitLane", c_int32),
    ]


class ACCStatic(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("_smVersion", c_wchar * 15), ("_acVersion", c_wchar * 15),
        ("numberOfSessions", c_int32), ("numCars", c_int32), ("carModel", c_wchar * 33),
        ("track", c_wchar * 33), ("playerName", c_wchar * 33), ("playerSurname", c_wchar * 33),
        ("playerNick", c_wchar * 33), ("sectorCount", c_int32),
    ]


_ACC_SESSION_TYPES = {
    -1: "unknown", 0: "practice", 1: "qualify", 2: "race", 3: "hotlap",
    4: "time_attack", 5: "drift", 6: "drag", 7: "hotstint", 8: "superpole",
}


class ACCAdapter(KunosAdapterBase):
    name = "acc"
    native_rate_hz = 333.0           # physics-step writes (~333 Hz measured)
    physics_struct = ACCPhysics
    graphics_struct = ACCGraphics
    static_struct = ACCStatic
    session_type_names = _ACC_SESSION_TYPES

    def _extend_frame(self, frame: TelemetryFrame, phys, gfx, static) -> None:
        frame.in_pit = bool(gfx.isInPit or gfx.isInPitLane)
        # Player world position: find our car in the multi-car coordinate array.
        for i in range(min(gfx.activeCars, 60)):
            if gfx.carID[i] == gfx.playerCarID:
                frame.pos_x = gfx.carCoordinates[i][0]
                frame.pos_y = gfx.carCoordinates[i][1]
                frame.pos_z = gfx.carCoordinates[i][2]
                break
        frame.extras = {
            "track": static.track,
            "car": static.carModel,
            "position": gfx.position,
            "distance_m": gfx.distanceTraveled,
        }
