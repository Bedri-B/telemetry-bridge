"""WebSocket live-stream broadcaster.

Serves ``ws://host:port``; every connected client receives:

* a ``hello`` message on connect (schema/version info)
* telemetry frames as JSON, throttled to the configured rate
* ``status`` messages on adapter connect/disconnect

Slow clients never block capture: sends fan out concurrently and a client
that cannot keep up is disconnected by the library's built-in write limits.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection, serve

from .. import __version__

log = logging.getLogger(__name__)


class WebSocketBroadcaster:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = port
        self._clients: set[ServerConnection] = set()
        self._server: Any = None

    async def start(self) -> None:
        self._server = await serve(
            self._handler,
            self.host,
            self.port,
            max_queue=32,          # per-client buffer; slow readers get dropped
        )
        log.info("WebSocket live stream on ws://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # ------------------------------------------------------------------

    async def _handler(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        log.info("client connected (%d total)", len(self._clients))
        try:
            await ws.send(json.dumps({
                "type": "hello",
                "app": "telemetry-bridge",
                "version": __version__,
            }))
            # We never expect inbound messages; just hold the connection open.
            async for _ in ws:
                pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)
            log.info("client disconnected (%d total)", len(self._clients))

    # ------------------------------------------------------------------

    async def broadcast(self, payload: dict[str, Any]) -> None:
        """Send one message to all clients without letting any of them block us."""
        if not self._clients:
            return
        data = json.dumps(payload, separators=(",", ":"))
        results = await asyncio.gather(
            *(self._send_one(ws, data) for ws in list(self._clients)),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception) and not isinstance(
                r, websockets.exceptions.ConnectionClosed
            ):
                log.warning("broadcast error: %s", r)

    @staticmethod
    async def _send_one(ws: ServerConnection, data: str) -> None:
        try:
            await ws.send(data)
        except websockets.exceptions.ConnectionClosed:
            pass
