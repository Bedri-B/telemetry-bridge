# Telemetry Bridge

A Windows desktop (headless CLI) telemetry bridge for racing simulators —
**iRacing**, **Assetto Corsa** and **Assetto Corsa Competizione** — with dual
output streams:

* **Live stream** — real-time telemetry over WebSockets, throttled to ~60 Hz
  for front-end consumption (lap/session events are never throttled away).
* **Historic log** — every frame at the simulator's native rate (up to
  ~333 Hz on AC/ACC) written to Parquet (or CSV), tagged with session and
  lap ids.

```
[sim shared memory]
      |   capture thread: adapter.poll() at native rate, auto-reconnect
      v
 SessionTracker  -- dedup, session/lap tagging, pause & reset detection
      |-----------------------------.
      v                             v
 HistoryLogger                   RateGate (~60 Hz)
 (Parquet/CSV, writer thread)       |
                                    v
                          WebSocketBroadcaster
```

## Quick start

```powershell
git clone <repo> && cd TelemetryBridge
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"          # add [iracing] for pyirsdk
.\.venv\Scripts\pip install -e ".[dev,iracing]"  # ... like this

# Run against the built-in mock sim (no game needed):
.\.venv\Scripts\python -m telemetry_bridge --sim mock

# Watch the live stream from a second terminal:
.\.venv\Scripts\python tools\ws_client.py

# Run against a real sim:
.\.venv\Scripts\python -m telemetry_bridge --config config.example.yaml --sim iracing
```

The bridge runs until Ctrl+C. If the sim is not running yet (or exits), the
bridge waits and reconnects automatically with exponential backoff.

## Configuration

Copy `config.example.yaml` and edit; every key is optional. Pass with
`--config my.yaml` (JSON is also accepted). Highlights:

| Key | Meaning |
|---|---|
| `sim` | `mock`, `iracing`, `ac`, or `acc` |
| `live.rate_hz` | WebSocket throttle (default 60) |
| `history.format` | `parquet` or `csv` |
| `history.roll_minutes` | max minutes per file chunk (crash-safety bound) |
| `tracker.log_paused` / `log_replay` | record frames during pause/replay |
| `mock.*` | scripted edge cases: pause, reset, duplicates, disconnect |

## WebSocket protocol

Connect to `ws://127.0.0.1:8765` (configurable). Messages are JSON:

* `{"type": "hello", ...}` — once, on connect
* `{"type": "status", "status": "iracing_connected" | "..._disconnected"}`
* `{"type": "telemetry", ...}` — normalized frame: `session_id`, `lap_id`,
  `state`, `speed_mps`, `rpm`, `gear`, `throttle`, `brake`, `lap`,
  `lap_dist_pct`, lap times (ms), fuel, position, plus
  `events: ["session_start" | "session_end" | "lap_complete" | "paused" | "resumed"]`
  when boundaries occur.

Units are normalized across sims: speed m/s, fuel liters, lap times integer
milliseconds, gear -1/0/1..n, pedals 0..1.

## Historic log

One Parquet file per session (rolled every `roll_minutes` for crash safety) in
`telemetry_logs/`, named `<utc-stamp>_<sim>_<session_id>.parquet`. Read with:

```python
import pyarrow.parquet as pq
table = pq.read_table("telemetry_logs/20260725T120000Z_mock_mock-ab12cd34ef56.parquet")
df = table.to_pandas()
df.groupby("lap_id").speed_mps.max()
```

File I/O runs on a dedicated writer thread behind a bounded queue — a slow
disk can never stall capture; overflow drops oldest data and is counted and
logged.

## Testing without a sim

The **mock adapter** generates realistic laps and can script every edge case
the state machine protects against:

```yaml
sim: mock
mock:
  rate_hz: 333          # stress the pipeline at AC-like rates
  pause_at: 20          # pause 5s in
  reset_at: 90          # session restart
  duplicate_every: 50   # duplicated shared-memory reads
  disconnect_at: 120    # sim exits; bridge must reconnect
```

Run the unit suite (no sims, no network):

```powershell
.\.venv\Scripts\python -m pytest
```

## Edge-case handling

| Situation | Behavior |
|---|---|
| Sim not running / exits | Adapter raises, capture supervisor reconnects with backoff; history file finalized; `status` broadcast |
| Session restart | Detected via session-clock rewind or lap-counter rewind; new `session_id`, `session_end`+`session_start` events, new log file |
| Pause | `paused`/`resumed` events; frames suppressed while paused (configurable) |
| Duplicate frames | Dropped on the sim's own tick counter (`SessionTick` / `packetId`) |
| Replay playback | Dropped by default (`tracker.log_replay: true` to record) |
| Slow WebSocket client | Per-client send queue; slow readers are disconnected, capture unaffected |
| Slow disk | Bounded queue, oldest-drop with counters; capture unaffected |
| Hard crash | Parquet chunks rolled every `roll_minutes`; at most the in-flight batch is lost. CSV mode flushes every batch |

## Adapter notes (sim-specific quirks)

* **iRacing** (`pyirsdk`): 60 Hz shared memory; frames pinned per-tick with
  `freeze_var_buffer_latest()`; `SessionTick` used for dedup; pause inferred
  from tick stall (iRacing has no pause flag); session type read from the
  SessionInfo YAML.
* **AC**: `acpmf_physics/graphics/static` MMFs, full AC 1.16 layout (Kunos
  shared-memory doc / Rombik's `sim_info.py`); physics at ~333 Hz, deduped on
  `packetId`; liveness via the graphics page (it updates even when paused).
* **ACC**: same MMF names, extended structs (Kunos SM doc v1.8.12 /
  rrennoir's pyAccSharedMemory); player world position resolved from the
  60-car coordinate array via `playerCarID`.
* Kunos sims report time *remaining*; `session_time` is `-sessionTimeLeft`,
  which preserves the monotonic-with-rewind property the reset detector needs.

## Reference projects

* [kutu/pyirsdk](https://github.com/kutu/pyirsdk) — canonical Python iRacing SDK binding (used here)
* [rrennoir/pyAccSharedMemory](https://github.com/rrennoir/pyAccSharedMemory) — ACC shared memory reference; ships Kunos' official documentation PDF
* Rombik's `sim_info.py` ([maintained copy](https://github.com/ac-custom-shaders-patch/acc-extension-apps/blob/master/apps/python/AccExtHelper/sim_info.py)) — de-facto standard AC ctypes mapping
* [CrewChiefV4](https://github.com/mrbelowski/CrewChiefV4) — battle-tested C# mappers for all three sims
* [SimHub](https://www.simhubdash.com/) — behavioral reference for multi-sim bridges
