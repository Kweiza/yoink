import json
from pathlib import Path
import config
from config import load_config, Config

def test_missing_file_returns_defaults(tmp_path):
    cfg, warnings = load_config(tmp_path)
    assert cfg.conflict_mode == "advisory"
    assert cfg.label_prefix == "yoink"
    assert cfg.lock_timeout_seconds == 10
    assert warnings == []

def test_valid_config_loads(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(json.dumps({
        "conflict_mode": "block",
        "label_prefix": "team_a",
        "lock_timeout_seconds": 20,
    }))
    cfg, warnings = load_config(tmp_path)
    assert cfg.conflict_mode == "block"
    assert cfg.label_prefix == "team_a"
    assert cfg.lock_timeout_seconds == 20
    assert warnings == []

def test_invalid_conflict_mode_falls_back(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(json.dumps({"conflict_mode": "warn"}))
    cfg, warnings = load_config(tmp_path)
    assert cfg.conflict_mode == "advisory"
    assert any("conflict_mode" in w for w in warnings)

def test_invalid_label_prefix_falls_back(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(json.dumps({"label_prefix": "1bad"}))
    cfg, warnings = load_config(tmp_path)
    assert cfg.label_prefix == "yoink"
    assert any("label_prefix" in w for w in warnings)

def test_lock_timeout_out_of_range_falls_back(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(json.dumps({"lock_timeout_seconds": 999}))
    cfg, warnings = load_config(tmp_path)
    assert cfg.lock_timeout_seconds == 10
    assert any("lock_timeout_seconds" in w for w in warnings)

def test_unknown_root_key_warns_only(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(json.dumps({"mystery": 1}))
    cfg, warnings = load_config(tmp_path)
    assert cfg.conflict_mode == "advisory"
    assert any("mystery" in w for w in warnings)

def test_reserved_namespaces_silent(tmp_path):
    # Phase 4: all _-prefixed keys are silently ignored.
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(json.dumps({
        "_phase4_reserved": {"heartbeat_interval_seconds": 300},
    }))
    _, warnings = load_config(tmp_path)
    assert warnings == []

def test_phase4_reserved_key_is_recognized_not_warned(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(
        '{"conflict_mode": "advisory", "_phase4_reserved": {"block_paths": []}}'
    )
    cfg, warnings = load_config(tmp_path)
    assert cfg.conflict_mode == "advisory"
    assert not any("_phase4_reserved" in w for w in warnings)

def test_heartbeat_cooldown_default_and_override(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(
        '{"heartbeat_cooldown_seconds": 60}'
    )
    cfg, warnings = load_config(tmp_path)
    assert cfg.heartbeat_cooldown_seconds == 60
    assert not warnings

def test_heartbeat_cooldown_out_of_range_falls_back_to_default(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(
        '{"heartbeat_cooldown_seconds": 0}'
    )
    cfg, warnings = load_config(tmp_path)
    assert cfg.heartbeat_cooldown_seconds == 120  # default
    assert any("heartbeat_cooldown_seconds" in w for w in warnings)

def test_stale_threshold_default_and_override(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(
        '{"stale_threshold_seconds": 1800}'
    )
    cfg, warnings = load_config(tmp_path)
    assert cfg.stale_threshold_seconds == 1800
    assert not warnings

def test_underscore_prefix_key_silently_ignored(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(
        '{"_phase4_reserved": {"legacy": "stuff"}, '
        '"_phase5_reserved": {"block_paths": []}, '
        '"_future_phaseN_reserved": {"x": 1}}'
    )
    cfg, warnings = load_config(tmp_path)
    assert not any("_phase4_reserved" in w for w in warnings)
    assert not any("_phase5_reserved" in w for w in warnings)
    assert not any("_future_phaseN_reserved" in w for w in warnings)

def test_stale_threshold_out_of_range_falls_back_to_default(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(
        '{"stale_threshold_seconds": 30}'
    )
    cfg, warnings = load_config(tmp_path)
    assert cfg.stale_threshold_seconds == 900  # default
    assert any("stale_threshold_seconds" in w for w in warnings)


def test_non_underscore_unknown_key_still_warns(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "yoink.config.json").write_text(
        '{"some_typo_key": 42}'
    )
    cfg, warnings = load_config(tmp_path)
    assert any("unknown key 'some_typo_key'" in w for w in warnings)
