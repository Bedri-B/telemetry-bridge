"""Minimal verification client for the live WebSocket stream.

Usage:  python tools/ws_client.py [ws://127.0.0.1:8765] [--seconds 10]

Prints a one-line summary per second (frame rate, latest speed/gear/lap) and
every event message in full — a quick way to confirm the bridge end-to-end
without a front-end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import websockets


async def run(url: str, seconds: float) -> None:
    async with websockets.connect(url) as ws:
        print(f"connected to {url}")
        frames = 0
        last_report = time.monotonic()
        latest = {}
        deadline = time.monotonic() + seconds if seconds else None
        while deadline is None or time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                raw = None
            if raw is not None:
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "telemetry":
                    frames += 1
                    latest = msg
                    if msg.get("events"):
                        print(f"EVENT {msg['events']}  lap={msg.get('lap')} "
                              f"session={msg.get('session_id')}")
                else:
                    print(f"{kind}: {msg}")
            now = time.monotonic()
            if now - last_report >= 1.0:
                rate = frames / (now - last_report)
                if latest:
                    print(
                        f"{rate:5.1f} msg/s | state={latest.get('state')} "
                        f"lap={latest.get('lap')} speed={latest.get('speed_mps', 0):5.1f} m/s "
                        f"gear={latest.get('gear')} rpm={latest.get('rpm', 0):5.0f}"
                    )
                else:
                    print(f"{rate:5.1f} msg/s (no telemetry yet)")
                frames = 0
                last_report = now


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", default="ws://127.0.0.1:8765")
    parser.add_argument("--seconds", type=float, default=0, help="0 = run forever")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.url, args.seconds))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
