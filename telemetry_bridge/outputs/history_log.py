"""Historic logger: native-rate telemetry to Parquet (or CSV) files.

Design goals:

* **Never block capture.** Frames go into a bounded queue; a dedicated
  writer thread does all file I/O (pyarrow calls are blocking). If the disk
  cannot keep up the oldest frames are dropped and counted, not the newest.
* **One file per session.** A new session id from the SessionTracker rolls
  the file. Files are named ``<start-iso>_<sim>_<session_id>.parquet``.
* **Crash-safe.** Parquet needs a footer on close, so the writer also rolls
  files every ``roll_minutes`` — a hard kill loses at most the current
  in-flight row group of the current chunk. CSV mode flushes every batch.
"""

from __future__ import annotations

import csv
import logging
import queue
import threading
import time
from dataclasses import dataclass, fields as dc_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..frames import TaggedFrame, TelemetryFrame

log = logging.getLogger(__name__)

_SENTINEL = object()

# Column order for both writers: frame scalars + tracker annotations.
_FRAME_COLUMNS = [
    f.name for f in dc_fields(TelemetryFrame) if f.name not in ("extras", "state")
]
_COLUMNS = ["session_id", "lap_id", "state", *_FRAME_COLUMNS]


@dataclass
class HistoryLogConfig:
    directory: str = "telemetry_logs"
    format: str = "parquet"          # "parquet" | "csv"
    batch_size: int = 256            # frames per row group / csv flush
    roll_minutes: float = 10.0       # max minutes per file chunk (crash safety)
    queue_size: int = 20_000         # ~60s of 333Hz data


def _row(tagged: TaggedFrame) -> dict[str, Any]:
    f = tagged.frame
    row = {name: getattr(f, name) for name in _FRAME_COLUMNS}
    row["state"] = f.state.value
    row["session_id"] = tagged.session_id
    row["lap_id"] = tagged.lap_id
    return row


class HistoryLogger:
    """Threaded, batching file logger. Feed with :meth:`write`; call
    :meth:`close_session` on adapter disconnect and :meth:`stop` on shutdown."""

    def __init__(self, config: HistoryLogConfig | None = None) -> None:
        self.config = config or HistoryLogConfig()
        self.dropped_frames = 0
        self.written_frames = 0
        self._q: queue.Queue = queue.Queue(maxsize=self.config.queue_size)
        self._thread: threading.Thread | None = None

    # -- producer side (async loop) -------------------------------------

    def start(self) -> None:
        Path(self.config.directory).mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(
            target=self._run, name="history-logger", daemon=True
        )
        self._thread.start()

    def write(self, tagged: TaggedFrame) -> None:
        try:
            self._q.put_nowait(tagged)
        except queue.Full:
            self.dropped_frames += 1
            if self.dropped_frames % 1000 == 1:
                log.warning(
                    "history log backpressure: %d frames dropped", self.dropped_frames
                )

    def close_session(self) -> None:
        """Ask the writer to finalize the current file (sim disconnected)."""
        self._q.put(("close_session",))

    def stop(self, timeout: float = 10.0) -> None:
        self._q.put(_SENTINEL)
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # -- writer thread ----------------------------------------------------

    def _run(self) -> None:
        writer: _ChunkWriter | None = None
        batch: list[dict[str, Any]] = []
        current_session = ""
        chunk_started = 0.0
        roll_s = self.config.roll_minutes * 60

        def flush() -> None:
            nonlocal writer
            if batch and writer is not None:
                writer.write_batch(batch)
                self.written_frames += len(batch)
                batch.clear()

        def close_chunk() -> None:
            nonlocal writer
            flush()
            if writer is not None:
                writer.close()
                writer = None

        while True:
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                # Idle: flush partial batches so data reaches disk promptly.
                flush()
                continue

            if item is _SENTINEL:
                close_chunk()
                return
            if isinstance(item, tuple) and item[0] == "close_session":
                close_chunk()
                current_session = ""
                continue

            tagged: TaggedFrame = item
            if tagged.session_id != current_session:
                close_chunk()
                current_session = tagged.session_id
                writer = self._open_writer(tagged)
                chunk_started = time.monotonic()
            elif writer is not None and time.monotonic() - chunk_started > roll_s:
                close_chunk()
                writer = self._open_writer(tagged)
                chunk_started = time.monotonic()

            batch.append(_row(tagged))
            if len(batch) >= self.config.batch_size:
                flush()

    def _open_writer(self, tagged: TaggedFrame) -> "_ChunkWriter":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stem = f"{stamp}_{tagged.frame.sim}_{tagged.session_id}"
        directory = Path(self.config.directory)
        if self.config.format == "csv":
            return _CsvChunkWriter(directory / f"{stem}.csv")
        return _ParquetChunkWriter(directory / f"{stem}.parquet")


# ---------------------------------------------------------------------------


class _ChunkWriter:
    def write_batch(self, rows: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class _ParquetChunkWriter(_ChunkWriter):
    def __init__(self, path: Path) -> None:
        import pyarrow as pa

        self._pa = pa
        self.path = path
        self._writer = None  # created lazily from the first batch's schema
        log.info("logging session to %s", path)

    def write_batch(self, rows: list[dict[str, Any]]) -> None:
        import pyarrow.parquet as pq

        table = self._pa.Table.from_pylist(rows)
        # Enforce stable column order.
        table = table.select([c for c in _COLUMNS if c in table.column_names])
        if self._writer is None:
            self._writer = pq.ParquetWriter(self.path, table.schema)
        self._writer.write_table(table)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
            log.info("finalized %s", self.path)


class _CsvChunkWriter(_ChunkWriter):
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=_COLUMNS, extrasaction="ignore")
        self._writer.writeheader()
        log.info("logging session to %s", path)

    def write_batch(self, rows: list[dict[str, Any]]) -> None:
        self._writer.writerows(rows)
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()
        log.info("finalized %s", self.path)
