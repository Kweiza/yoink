import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))


@pytest.fixture(autouse=True)
def _isolate_metric_log(tmp_path, monkeypatch):
    """Redirect telemetry jsonl persistence to a per-test tmp file.

    Prevents tests from polluting the real ~/.claude/logs/yoink/metric.jsonl.
    Tests that want to inspect jsonl content can read
    `os.environ['YOINK_METRIC_LOG']` inside the test body.
    """
    monkeypatch.setenv("YOINK_METRIC_LOG", str(tmp_path / "metric.jsonl"))
