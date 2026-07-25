# Telemetry Bridge — Design

*2026-07-25 — initial design, approved scope: iRacing/AC/ACC capture,
dual-stream output (60 Hz WebSocket live + native-rate Parquet history),
mock generator, pytest suite. Windows, headless CLI.*

## Goals

1. Extract live telemetry via each sim's shared memory, surviving sim
   restarts without operator intervention.
2. Two consumers with different needs from one capture pass:
   a front-end wanting ~60 Hz *now*, and an analyst wanting *everything*
   on disk at native rate.
3. Sim quirks (duplicate frames, pauses, session resets, replay mode,
   crashes) must be absorbed in one place, not leak to consumers.
4. Everything testable without a simulator installed.

## Architecture

Single process, three execution contexts:

| Context | Role | Why separate |
|---|---|---|
| Capture thread | `adapter.poll()` loop + reconnect supervisor | shared-memory polling is blocking/synchronous; must never be starved by I/O |
| asyncio loop | SessionTracker -> fan-out -> WebSocket sends | natural home for websockets; all state-machine logic runs single-threaded here |
| Writer thread | Parquet/CSV batch writes | pyarrow I/O is blocking; bounded queue decouples disk from capture |

Handoffs: capture -> loop via `call_soon_threadsafe` into a bounded
`asyncio.Queue` (drop+count on overflow); loop -> writer via bounded
`queue.Queue` (drop-oldest+count on overflow). Backpressure therefore
degrades gracefully and observably instead of stalling capture.

## Adapter pattern

`SimAdapter` (connect / poll(timeout) / close, raises `AdapterError` on
disconnect) is the entire sim-facing surface. Everything simulator-specific —
struct layouts, unit conversions, gear encodings, pause heuristics, liveness
detection — stays inside one adapter module:

* `iracing.py` — pyirsdk; 60 Hz; dedup on `SessionTick`; pause = tick stall.
* `assetto.py` — ctypes over `acpmf_*` MMFs, full AC 1.16 layout; ~333 Hz.
* `acc.py` — ACC's extended layouts (prefix structs covering consumed fields).
* `kunos_common.py` — shared AC/ACC base: MMF handling, tear-resistant
  snapshot reads, staleness liveness check via the graphics page.
* `mock.py` — synthetic laps + scripted edge cases; drives tests and demos.

Normalized units at the adapter boundary: m/s, liters, ms, gear -1/0/1..n,
pedals 0..1. The rest of the system never sees a sim-specific value.

## Session state machine (`session.py`)

Pure function-of-input logic (no clocks, no I/O) so it is exhaustively unit
testable. Responsibilities: duplicate-tick drop (state-change aware, since
tick counters freeze during pauses), session identity (uuid per session;
reset detected via session-clock rewind > 2 s or lap-counter rewind),
lap ids + `lap_complete` events, `paused`/`resumed` events,
replay/menu suppression, disconnect closure.

## Dual outputs

* **Live:** `RateGate` latest-value sampling by frame timestamp (works under
  test acceleration); event-carrying frames bypass the gate so clients never
  miss boundaries. Broadcast fan-out is concurrent; a slow client is dropped
  by the websocket library's send-queue cap rather than blocking others.
* **History:** one file per session, rolled every `roll_minutes` so a hard
  kill loses at most one in-flight batch (Parquet needs its footer; rolling
  bounds the blast radius). CSV mode trades size for flush-per-batch
  durability. Stable column order; session_id/lap_id/state as columns.

## Testing strategy

* Unit: session state machine (dedup/pause/reset/lap/disconnect), rate gate,
  config merge, mock adapter scenario scripting, Parquet/CSV round-trip and
  per-session file rolling.
* Integration (manual/smoke): `--sim mock --duration N` end-to-end run with
  `tools/ws_client.py` attached; verifies both streams live.
* Real-sim verification is by nature manual; adapter risk is contained by
  keeping adapters thin over verbatim published struct layouts (sources in
  README).

## Rejected alternatives

* **asyncio-only capture** (no capture thread): shared-memory polling and
  pyirsdk's event waits are blocking; wrapping every poll in an executor
  costs more than one dedicated thread at 333 Hz.
* **Queue per WebSocket client with full-rate delivery**: front-ends want
  latest-value at a bounded rate; sampling is the correct semantic and keeps
  slow clients from mattering.
* **ACC Broadcasting UDP API**: richer multi-car/timing data, but overkill
  for single-car telemetry and adds a config burden (broadcasting.json);
  noted as a future extension.
