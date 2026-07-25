"""Adapter registry: maps config sim ids to adapter factories."""

from __future__ import annotations

from typing import Callable

from .base import AdapterError, SimAdapter
from .mock import MockAdapter, MockScenario

__all__ = ["AdapterError", "SimAdapter", "MockAdapter", "MockScenario", "create_adapter"]


def create_adapter(sim: str, config) -> SimAdapter:
    """Instantiate the adapter for ``sim`` (mock | iracing | ac | acc).

    Sim-specific modules are imported lazily so a missing optional dependency
    (e.g. pyirsdk) only matters if that sim is actually selected.
    """
    sim = sim.lower()
    if sim == "mock":
        return MockAdapter(config.mock)
    if sim == "iracing":
        from .iracing import IRacingAdapter

        return IRacingAdapter()
    if sim == "ac":
        from .assetto import ACAdapter

        return ACAdapter()
    if sim == "acc":
        from .acc import ACCAdapter

        return ACCAdapter()
    raise ValueError(f"unknown sim '{sim}' (expected mock, iracing, ac or acc)")
