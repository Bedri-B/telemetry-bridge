"""CLI entry point: ``python -m telemetry_bridge`` or ``telemetry-bridge``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .app import BridgeApp
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="telemetry-bridge",
        description="Sim-racing telemetry bridge: WebSocket live stream + Parquet history log",
    )
    parser.add_argument("-c", "--config", help="path to YAML/JSON config file")
    parser.add_argument(
        "-s", "--sim", choices=["mock", "iracing", "ac", "acc"],
        help="override configured simulator",
    )
    parser.add_argument(
        "--duration", type=float, default=None,
        help="run for N seconds then exit (smoke tests); default: run until Ctrl+C",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if args.sim:
        config.sim = args.sim

    logging.basicConfig(
        level="DEBUG" if args.verbose else config.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    app = BridgeApp(config)
    try:
        asyncio.run(app.run(duration_s=args.duration))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
