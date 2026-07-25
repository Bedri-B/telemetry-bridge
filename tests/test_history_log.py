import time

import pyarrow.parquet as pq

from telemetry_bridge.frames import TaggedFrame, TelemetryFrame
from telemetry_bridge.outputs.history_log import HistoryLogConfig, HistoryLogger


def tagged(tick, session_id="s1", lap_id=0):
    return TaggedFrame(
        TelemetryFrame(sim="mock", tick=tick, session_time=tick / 60.0),
        session_id=session_id,
        lap_id=lap_id,
    )


def run_logger(tmp_path, fmt, frames):
    logger = HistoryLogger(
        HistoryLogConfig(directory=str(tmp_path), format=fmt, batch_size=16)
    )
    logger.start()
    for f in frames:
        logger.write(f)
    logger.stop()
    return logger


def test_parquet_roundtrip(tmp_path):
    logger = run_logger(tmp_path, "parquet", [tagged(i) for i in range(100)])
    files = list(tmp_path.glob("*.parquet"))
    assert len(files) == 1
    table = pq.read_table(files[0])
    assert table.num_rows == 100
    assert logger.written_frames == 100
    cols = table.column_names
    for expected in ("session_id", "lap_id", "tick", "speed_mps", "state"):
        assert expected in cols
    ticks = table.column("tick").to_pylist()
    assert ticks == list(range(100))


def test_csv_fallback(tmp_path):
    run_logger(tmp_path, "csv", [tagged(i) for i in range(10)])
    files = list(tmp_path.glob("*.csv"))
    assert len(files) == 1
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 11  # header + 10 rows
    assert lines[0].startswith("session_id,lap_id,state")


def test_new_session_rolls_file(tmp_path):
    frames = [tagged(i, session_id="s1") for i in range(20)]
    frames += [tagged(i, session_id="s2") for i in range(20)]
    run_logger(tmp_path, "parquet", frames)
    files = sorted(tmp_path.glob("*.parquet"))
    assert len(files) == 2
    for f in files:
        assert pq.read_table(f).num_rows == 20


def test_close_session_finalizes_file(tmp_path):
    logger = HistoryLogger(
        HistoryLogConfig(directory=str(tmp_path), format="parquet", batch_size=8)
    )
    logger.start()
    for i in range(10):
        logger.write(tagged(i))
    logger.close_session()
    deadline = time.time() + 5
    while time.time() < deadline:
        files = list(tmp_path.glob("*.parquet"))
        if files and _readable(files[0]):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("parquet file was not finalized after close_session")
    logger.stop()


def _readable(path):
    try:
        return pq.read_table(path).num_rows == 10
    except Exception:
        return False
