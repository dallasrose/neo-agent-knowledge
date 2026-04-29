from pathlib import Path

import pytest

from neo.integrations.hermes.config import HermesNeoConfig


def test_load_defaults_when_missing(tmp_path: Path):
    config = HermesNeoConfig.load(tmp_path)
    assert config.agent_name == "default"
    assert config.recall_mode == "signals-first"
    assert config.auto_ingest == "explicit-only"
    assert config.signal_token_budget <= config.token_budget


def test_load_from_hermes_home_json(tmp_path: Path):
    (tmp_path / "neo.json").write_text(
        '{"agent_name":"atlas","top_k":6,"include_sparks":false}'
    )
    config = HermesNeoConfig.load(tmp_path)
    assert config.agent_name == "atlas"
    assert config.top_k == 6
    assert config.include_sparks is False


def test_save_round_trips(tmp_path: Path):
    original = HermesNeoConfig(agent_name="atlas", top_k=8, signal_token_budget=250)
    path = original.save(tmp_path)

    assert path == tmp_path / "neo.json"
    loaded = HermesNeoConfig.load(tmp_path)
    assert loaded.agent_name == "atlas"
    assert loaded.top_k == 8
    assert loaded.signal_token_budget == 250


def test_invalid_threshold_raises(tmp_path: Path):
    (tmp_path / "neo.json").write_text('{"min_confidence": 2}')
    with pytest.raises(ValueError, match="min_confidence"):
        HermesNeoConfig.load(tmp_path)
