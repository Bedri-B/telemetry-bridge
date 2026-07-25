"""Adapter interface: everything simulator-specific lives behind this."""

from __future__ import annotations

import abc

from ..frames import TelemetryFrame


class AdapterError(Exception):
    """Raised by adapters on unrecoverable read/connect errors.

    The capture supervisor treats this as "sim went away": it closes the
    adapter and retries connection with backoff.
    """


class SimAdapter(abc.ABC):
    """Blocking, synchronous adapter for one simulator.

    Lifecycle::

        adapter.connect()      # raises AdapterError if the sim is not up
        while ...:
            frame = adapter.poll(timeout)   # None on timeout / no new data
        adapter.close()

    Adapters are driven from a dedicated capture thread, so ``poll`` may block
    briefly (e.g. waiting on the sim's next tick) but must honor ``timeout``.
    Implementations must be safe to ``close()`` from any state and to
    re-``connect()`` after a close.
    """

    #: short id used in config, logs and frame.sim ("iracing", "ac", ...)
    name: str = "abstract"

    #: telemetry rate the sim writes at (Hz); informational, used for docs/UI
    native_rate_hz: float = 60.0

    @abc.abstractmethod
    def connect(self) -> None:
        """Attach to the sim. Raise AdapterError if unavailable."""

    @abc.abstractmethod
    def poll(self, timeout: float) -> TelemetryFrame | None:
        """Return the next telemetry frame.

        Returns None if no new data arrived within ``timeout`` seconds.
        Raises AdapterError if the sim disconnected.
        """

    @abc.abstractmethod
    def close(self) -> None:
        """Detach from the sim, releasing shared memory handles."""
