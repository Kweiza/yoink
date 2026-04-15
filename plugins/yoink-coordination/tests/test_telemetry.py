"""Tests for lib/telemetry.py."""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import telemetry  # noqa: E402


def _parse_metric_lines(captured_stderr: str) -> list[dict]:
    """Extract all [yoink-metric] JSON payloads from captured stderr."""
    out = []
    for line in captured_stderr.splitlines():
        m = re.match(r"\[yoink-metric\] (\{.*\})$", line)
        if m:
            out.append(json.loads(m.group(1)))
    return out


def test_emit_produces_single_json_line_on_stderr(capsys):
    telemetry.emit("test_hook", "test_metric", foo=1, bar="baz")
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout untouched
    lines = _parse_metric_lines(captured.err)
    assert len(lines) == 1
    payload = lines[0]
    assert payload["hook"] == "test_hook"
    assert payload["metric"] == "test_metric"
    assert payload["foo"] == 1
    assert payload["bar"] == "baz"
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", payload["ts"])


def test_emit_field_order_arbitrary_but_keys_consistent(capsys):
    telemetry.emit("h", "m", a=1, b=2)
    telemetry.emit("h", "m", b=2, a=1)
    lines = _parse_metric_lines(capsys.readouterr().err)
    assert len(lines) == 2
    assert set(lines[0].keys()) == set(lines[1].keys())


def test_latency_timer_emits_on_success(capsys):
    with telemetry.LatencyTimer("some_hook"):
        pass
    lines = _parse_metric_lines(capsys.readouterr().err)
    assert len(lines) == 1
    assert lines[0]["hook"] == "some_hook"
    assert lines[0]["metric"] == "latency"
    assert isinstance(lines[0]["duration_ms"], int)
    assert lines[0]["duration_ms"] >= 0


def test_latency_timer_emits_on_exception(capsys):
    try:
        with telemetry.LatencyTimer("crashy_hook"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    lines = _parse_metric_lines(capsys.readouterr().err)
    assert len(lines) == 1
    assert lines[0]["hook"] == "crashy_hook"
    assert lines[0]["metric"] == "latency"


def test_latency_timer_does_not_suppress_exception(capsys):
    raised = False
    try:
        with telemetry.LatencyTimer("h"):
            raise ValueError("nope")
    except ValueError:
        raised = True
    assert raised


def test_path_hash_stable_8_hex_chars():
    h1 = telemetry.path_hash("src/main.py")
    h2 = telemetry.path_hash("src/main.py")
    assert h1 == h2
    assert re.match(r"^[0-9a-f]{8}$", h1)


def test_path_hash_differs_for_different_paths():
    assert telemetry.path_hash("a.py") != telemetry.path_hash("b.py")


def test_emit_always_includes_common_keys(capsys):
    """Common key set {ts, hook, metric} must be present even with no fields."""
    telemetry.emit("h", "m")
    line = _parse_metric_lines(capsys.readouterr().err)[0]
    assert {"ts", "hook", "metric"} <= line.keys()


def test_emit_rejects_reserved_field_kwargs(capsys):
    """Caller cannot pass ts/hook/metric as kwargs — spec §9.5 stability."""
    import pytest
    for bad in ("ts", "hook", "metric"):
        with pytest.raises(TypeError, match="reserved"):
            telemetry.emit("h", "m", **{bad: "override"})
