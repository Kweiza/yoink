"""Centralized telemetry emission for Phase 5.

Emits `[yoink-metric] {...json}` lines on stderr AND appends them to a
local jsonl log at `~/.claude/logs/yoink/metric.jsonl` for later
aggregation. Additive only — does not replace existing `[yoink] ...`
human lines. See spec §5.2 / §5.3.
"""
from __future__ import annotations
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    """ISO 8601 UTC with Zulu suffix, second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_path() -> Path:
    """Resolve jsonl log path. Overridable via YOINK_METRIC_LOG for tests."""
    override = os.environ.get("YOINK_METRIC_LOG")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "logs" / "yoink" / "metric.jsonl"


def _persist(line: str) -> None:
    """Append one JSON line to the jsonl log. Fail-silent on IOError
    so a broken log sink never blocks the hook."""
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def emit(hook: str, metric: str, /, **fields: Any) -> None:
    """Emit a single `[yoink-metric] {…}` JSON line to stderr AND persist
    the same line to the jsonl log.

    Fields merge into the payload after common keys (ts, hook, metric). Caller
    kwargs cannot shadow the common keys — doing so raises TypeError so
    spec↔runtime crosscheck (spec §9.5) stays stable.
    """
    reserved = {"ts", "hook", "metric"} & fields.keys()
    if reserved:
        raise TypeError(
            f"emit(): fields shadow reserved common keys: {sorted(reserved)}"
        )
    payload = {"ts": _now_iso(), "hook": hook, "metric": metric}
    payload.update(fields)
    line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    print(f"[yoink-metric] {line}", file=sys.stderr)
    _persist(line)


class LatencyTimer:
    """Context manager that emits a `latency` metric on exit, even on exception.

    Usage:
        with LatencyTimer("session_start"):
            run_hook_body()

    The latency line is emitted in `__exit__`, which Python guarantees runs on
    exceptions. Exception propagation is NOT suppressed (returns False).
    """

    def __init__(self, hook: str) -> None:
        self.hook = hook
        self._t0: float = 0.0

    def __enter__(self) -> "LatencyTimer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        duration_ms = int((time.perf_counter() - self._t0) * 1000)
        emit(self.hook, "latency", duration_ms=duration_ms)
        return False


def path_hash(path: str) -> str:
    """SHA1-based 8-char anonymized identifier for a file path."""
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
