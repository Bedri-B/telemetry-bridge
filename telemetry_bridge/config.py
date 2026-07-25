"""YAML/JSON configuration loading with typed defaults."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .adapters.mock import MockScenario
from .outputs.history_log import HistoryLogConfig
from .session import SessionTrackerConfig


@dataclass
class LiveStreamConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    rate_hz: float = 60.0


@dataclass
class CaptureConfig:
    poll_timeout_s: float = 0.1      # max block per adapter poll
    reconnect_min_s: float = 1.0     # backoff floor when sim is away
    reconnect_max_s: float = 10.0    # backoff ceiling


@dataclass
class BridgeConfig:
    sim: str = "mock"                # mock | iracing | ac | acc
    live: LiveStreamConfig = field(default_factory=LiveStreamConfig)
    history: HistoryLogConfig = field(default_factory=HistoryLogConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    tracker: SessionTrackerConfig = field(default_factory=SessionTrackerConfig)
    mock: MockScenario = field(default_factory=MockScenario)
    log_level: str = "INFO"


def _merge(dc: Any, data: dict[str, Any], path: str) -> None:
    for key, value in data.items():
        if not hasattr(dc, key):
            raise ValueError(f"unknown config key: {path}{key}")
        current = getattr(dc, key)
        if isinstance(value, dict) and hasattr(current, "__dataclass_fields__"):
            _merge(current, value, f"{path}{key}.")
        else:
            setattr(dc, key, value)


def load_config(path: str | Path | None) -> BridgeConfig:
    """Load config from a YAML or JSON file; missing keys keep defaults."""
    config = BridgeConfig()
    if path is None:
        return config
    text = Path(path).read_text(encoding="utf-8")
    data = (
        json.loads(text) if str(path).endswith(".json") else yaml.safe_load(text)
    ) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config root must be a mapping, got {type(data).__name__}")
    _merge(config, data, "")
    return config
