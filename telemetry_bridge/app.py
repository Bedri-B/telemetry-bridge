"""Application wiring: capture thread -> session tracker -> dual outputs.

Data flow::

    [sim shared memory]
          |  (capture thread: adapter.poll at native rate, auto-reconnect)
          v
    asyncio queue  --> SessionTracker (dedup, tagging, events)
                          |--> HistoryLogger (every frame, own writer thread)
                          '--> RateGate -> WebSocketBroadcaster (~60 Hz)
"""

from __future__ import annotations

import asyncio
import logging
import threading

from .adapters import AdapterError, create_adapter
from .config import BridgeConfig
from .outputs import HistoryLogger, WebSocketBroadcaster
from .session import SessionTracker
from .throttle import RateGate

log = logging.getLogger(__name__)


class BridgeApp:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.tracker = SessionTracker(config.tracker)
        self.logger = HistoryLogger(config.history)
        self.broadcaster = WebSocketBroadcaster(config.live.host, config.live.port)
        self.gate = RateGate(config.live.rate_hz)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=4096)
        self._stop = threading.Event()
        self._dropped = 0

    # ------------------------------------------------------------------

    async def run(self, duration_s: float | None = None) -> None:
        """Run until cancelled (Ctrl+C) or for ``duration_s`` seconds."""
        loop = asyncio.get_running_loop()
        self.logger.start()
        if self.config.live.enabled:
            await self.broadcaster.start()

        capture = threading.Thread(
            target=self._capture_loop, args=(loop,), name="capture", daemon=True
        )
        capture.start()

        processor = asyncio.create_task(self._process())
        try:
            if duration_s is None:
                await processor
            else:
                await asyncio.wait_for(asyncio.shield(processor), timeout=duration_s)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        finally:
            self._stop.set()
            processor.cancel()
            capture.join(timeout=5)
            self.logger.stop()
            if self.config.live.enabled:
                await self.broadcaster.stop()
            log.info(
                "shutdown: %d frames logged, %d dropped (history), %d dropped (ingest)",
                self.logger.written_frames,
                self.logger.dropped_frames,
                self._dropped,
            )

    # -- capture thread ----------------------------------------------------

    def _capture_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        cap = self.config.capture
        backoff = cap.reconnect_min_s
        while not self._stop.is_set():
            adapter = create_adapter(self.config.sim, self.config)
            try:
                adapter.connect()
            except AdapterError as exc:
                log.debug("sim unavailable (%s); retrying in %.1fs", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, cap.reconnect_max_s)
                continue
            except Exception:
                log.exception("adapter connect failed unexpectedly")
                self._stop.wait(backoff)
                continue

            backoff = cap.reconnect_min_s
            self._emit(loop, ("status", f"{adapter.name}_connected"))
            log.info("connected to %s", adapter.name)
            try:
                while not self._stop.is_set():
                    frame = adapter.poll(cap.poll_timeout_s)
                    if frame is not None:
                        self._emit(loop, ("frame", frame))
            except AdapterError as exc:
                log.info("sim disconnected: %s", exc)
            except Exception:
                log.exception("adapter poll failed; treating as disconnect")
            finally:
                adapter.close()
                self._emit(loop, ("status", f"{adapter.name}_disconnected"))

    def _emit(self, loop: asyncio.AbstractEventLoop, item: tuple) -> None:
        def _put() -> None:
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                self._dropped += 1

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass  # loop already closed during shutdown

    # -- async consumer ------------------------------------------------------

    async def _process(self) -> None:
        while True:
            kind, payload = await self._queue.get()
            if kind == "status":
                if payload.endswith("_disconnected"):
                    self.tracker.sim_disconnected()
                    self.logger.close_session()
                    self.gate.reset()
                await self.broadcaster.broadcast({"type": "status", "status": payload})
                continue

            tagged = self.tracker.process(payload)
            if tagged is None:
                continue
            self.logger.write(tagged)
            if self.gate.allow(payload.wall_time, has_events=bool(tagged.events)):
                message = tagged.to_dict()
                message["type"] = "telemetry"
                await self.broadcaster.broadcast(message)
