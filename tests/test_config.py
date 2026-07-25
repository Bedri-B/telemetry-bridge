import pytest

from telemetry_bridge.config import load_config


def test_defaults_without_file():
    config = load_config(None)
    assert config.sim == "mock"
    assert config.live.rate_hz == 60.0
    assert config.history.format == "parquet"


def test_yaml_overrides(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "sim: acc\nlive:\n  port: 9000\nhistory:\n  format: csv\nmock:\n  rate_hz: 333\n"
    )
    config = load_config(path)
    assert config.sim == "acc"
    assert config.live.port == 9000
    assert config.live.host == "127.0.0.1"     # untouched default
    assert config.history.format == "csv"
    assert config.mock.rate_hz == 333


def test_json_config(tmp_path):
    path = tmp_path / "c.json"
    path.write_text('{"sim": "iracing", "live": {"rate_hz": 30}}')
    config = load_config(path)
    assert config.sim == "iracing"
    assert config.live.rate_hz == 30


def test_unknown_key_rejected(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("live:\n  prot: 1234\n")
    with pytest.raises(ValueError, match="unknown config key: live.prot"):
        load_config(path)
