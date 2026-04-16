"""Tests for hooks/stop.py.

v0.3.28: Stop hook is latency-only. Heartbeat machinery retired with
release detection moved to the Actions workflow.
"""
import json as _json
import sys
from pathlib import Path

# conftest handles lib/; add hooks/
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))

import stop  # noqa


def test_stop_returns_zero():
    assert stop.main() == 0


def test_stop_emits_latency(capsys):
    stop.main()
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if ln.startswith("[yoink-metric] ")]
    parsed = [_json.loads(ln.split(" ", 1)[1]) for ln in lines]
    latency = [p for p in parsed if p["metric"] == "latency"]
    assert len(latency) == 1
    assert latency[0]["hook"] == "stop"
    assert isinstance(latency[0]["duration_ms"], int)
