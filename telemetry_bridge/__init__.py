"""Telemetry Bridge: dual-stream sim-racing telemetry capture.

Captures live telemetry from racing simulators (iRacing, Assetto Corsa,
Assetto Corsa Competizione) via shared memory, normalizes it into a common
frame schema, and fans it out to:

* a throttled (~60 Hz) WebSocket live stream for front-end consumption
* a native-rate Parquet/CSV historic log with session and lap tagging
"""

__version__ = "0.1.0"
