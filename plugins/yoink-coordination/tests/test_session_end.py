"""SessionEnd tests after v0.3.15.

Under the "task lives until primary-merge" rule, SessionEnd does NOT touch
the issue body, declared_files, or task_cache. It only emits the latency
metric. The legacy tests that asserted entry removal / issue close /
stamp clear have been deleted because they test behavior that
contradicted the rule.
"""
import sys
import json
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))


def _metric_lines(err: str) -> list:
    out = []
    for ln in err.splitlines():
        if ln.startswith("[yoink-metric] "):
            out.append(json.loads(ln.split(" ", 1)[1]))
    return out


def test_session_end_emits_latency_only(capsys):
    import session_end
    rc = session_end.main()
    assert rc == 0
    parsed = _metric_lines(capsys.readouterr().err)
    latency = [p for p in parsed if p["metric"] == "latency"]
    assert len(latency) == 1
    assert latency[0]["hook"] == "session_end"


def test_session_end_does_not_touch_task_cache(tmp_path, monkeypatch):
    """v0.3.15 critical: a stamp written for an active task must survive
    a session ending. Only stop.py's merge-detected entry-empty path
    should clear the stamp."""
    import importlib
    monkeypatch.setenv("YOINK_TASK_CACHE_ROOT", str(tmp_path / "cache"))
    import task_cache as tc
    importlib.reload(tc)
    import session_end as hook
    importlib.reload(hook)

    tc.mark_set("/wt", "main")
    assert tc.is_set("/wt", "main") is True
    rc = hook.main()
    assert rc == 0
    assert tc.is_set("/wt", "main") is True
