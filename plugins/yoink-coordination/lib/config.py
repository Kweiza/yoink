"""Config loader for yoink-coordination. See spec §6."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
import constants

@dataclass
class Config:
    conflict_mode: str = constants.DEFAULT_CONFLICT_MODE
    label_prefix: str = constants.DEFAULT_LABEL_PREFIX
    lock_timeout_seconds: int = constants.DEFAULT_LOCK_TIMEOUT_SECONDS
    heartbeat_cooldown_seconds: int = constants.DEFAULT_HEARTBEAT_COOLDOWN_SECONDS
    stale_threshold_seconds: int = constants.DEFAULT_STALE_THRESHOLD_SECONDS

KNOWN_ROOT_KEYS = {
    "conflict_mode", "label_prefix", "lock_timeout_seconds",
    "heartbeat_cooldown_seconds", "stale_threshold_seconds",
    # Legacy keys — recognized (no warning) but ignored in v0.3.26+.
    # Release detection moved to the GitHub Actions workflow; Stop hook
    # no longer needs the primary branch name.
    "primary_branch",
}

def load_config(repo_root: Path) -> Tuple[Config, List[str]]:
    warnings: List[str] = []
    path = repo_root / constants.CONFIG_FILENAME
    if not path.exists():
        return Config(), warnings
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        warnings.append(f"config: failed to read {path}: {e}; using defaults")
        return Config(), warnings

    cfg = Config()
    for key in raw:
        if key.startswith("_"):
            continue  # phase4 spec §3.2: _-prefix keys reserved for future/past phases
        if key not in KNOWN_ROOT_KEYS:
            warnings.append(f"config: unknown key '{key}' ignored")

    if "conflict_mode" in raw:
        v = raw["conflict_mode"]
        if v in constants.ALLOWED_CONFLICT_MODES:
            cfg.conflict_mode = v
        else:
            warnings.append(f"config: conflict_mode '{v}' not in {constants.ALLOWED_CONFLICT_MODES}; using default")

    if "label_prefix" in raw:
        v = raw["label_prefix"]
        if isinstance(v, str) and re.match(constants.LABEL_PREFIX_PATTERN, v):
            cfg.label_prefix = v
        else:
            warnings.append(f"config: label_prefix '{v}' invalid; using default")

    if "lock_timeout_seconds" in raw:
        v = raw["lock_timeout_seconds"]
        if isinstance(v, int) and 1 <= v <= 60:
            cfg.lock_timeout_seconds = v
        else:
            warnings.append(f"config: lock_timeout_seconds '{v}' out of range [1,60]; using default")

    if "heartbeat_cooldown_seconds" in raw:
        v = raw["heartbeat_cooldown_seconds"]
        if isinstance(v, int) and 1 <= v <= 3600:
            cfg.heartbeat_cooldown_seconds = v
        else:
            warnings.append(f"config: heartbeat_cooldown_seconds '{v}' out of range [1,3600]; using default")

    if "stale_threshold_seconds" in raw:
        v = raw["stale_threshold_seconds"]
        if isinstance(v, int) and 60 <= v <= 86400:
            cfg.stale_threshold_seconds = v
        else:
            warnings.append(f"config: stale_threshold_seconds '{v}' out of range [60,86400]; using default")

    # `primary_branch` was a Config field in v0.3.7~0.3.25. Release detection
    # moved into the GitHub Actions workflow in v0.3.19+ and landed as the
    # sole mechanism in v0.3.25. As of v0.3.26 the client no longer needs
    # the primary branch at all. Existing yoink.config.json files with a
    # `primary_branch` key still load cleanly — the unknown-key warning
    # above covers it if anyone typoed.

    return cfg, warnings
