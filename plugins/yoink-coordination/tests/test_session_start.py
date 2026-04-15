"""SessionStart tests after v0.3.15.

Self-heal removed (heartbeat-based eviction conflicted with the
"task lives until primary-merge" rule). task_cache.clear removed (stamp
follows task lifetime, not session lifetime). SessionStart now only
emits latency and prints peer activity.
"""
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))


def _metric_lines(err: str) -> list:
    out = []
    for ln in err.splitlines():
        m = re.match(r"\[yoink-metric\] (\{.*\})$", ln)
        if m:
            out.append(json.loads(m.group(1)))
    return out


@patch("github.label_exists", return_value=False)
@patch("github.gh_auth_ok", return_value=True)
@patch("context.build_context")
def test_missing_label_skips(bc, auth, labels, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from context import Context
    bc.return_value = Context("alice", "o/r", "main", str(tmp_path),
                              None, "uuid-1", None, "2026-04-14T10:00:00Z")
    import session_start
    rc = session_start.main()
    assert rc == 0
    err = capsys.readouterr().err
    assert "yoink:status" in err


@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("github.edit_issue_body", return_value=True)
@patch("github.create_status_issue")
@patch("github.add_label", return_value=True)
@patch("github.list_other_status_issues_open", return_value=[])
@patch("context.build_context")
def test_session_start_does_not_touch_my_issue(bc, other, addlbl, create, edit, auth, labels, tmp_path, monkeypatch):
    """v0.3.15: SessionStart never creates an issue, never edits a body,
    never adds a label. Lazy creation and all body management belong to
    PreToolUse / stop.py."""
    monkeypatch.chdir(tmp_path)
    from context import Context
    bc.return_value = Context("alice", "o/r", "main", str(tmp_path),
                              None, "uuid-1", None, "2026-04-15T10:00:00Z")
    import session_start
    rc = session_start.main()
    assert rc == 0
    create.assert_not_called()
    edit.assert_not_called()
    addlbl.assert_not_called()


def test_session_start_does_not_clear_task_cache(tmp_path, monkeypatch):
    """v0.3.15 critical: a session restart on the same (worktree, branch)
    must NOT wipe the stamp — the task is still alive (declared_files
    persist across sessions until merged)."""
    import importlib
    monkeypatch.setenv("YOINK_TASK_CACHE_ROOT", str(tmp_path / "cache"))
    import task_cache as tc
    importlib.reload(tc)
    import session_start as hook
    importlib.reload(hook)

    tc.mark_set("/wt", "main")
    assert tc.is_set("/wt", "main") is True

    from types import SimpleNamespace
    fake_ctx = SimpleNamespace(
        login="alice", repo_name_with_owner="o/r",
        worktree_path="/wt", branch="main",
        session_id="s", claude_session_id="ccs",
        task_issue=None, started_at="2026-04-15T10:00:00Z",
    )
    with patch.object(hook.github, "gh_auth_ok", return_value=True), \
         patch.object(hook.github, "label_exists", return_value=False), \
         patch.object(hook.ctx_mod, "build_context", return_value=fake_ctx), \
         patch.object(hook.cfg_mod, "load_config",
                      return_value=(SimpleNamespace(label_prefix="yoink",
                                                     stale_threshold_seconds=900), [])):
        rc = hook.main()
    assert rc == 0
    assert tc.is_set("/wt", "main") is True


def test_session_start_emits_latency_even_on_early_return(capsys, monkeypatch):
    import session_start
    monkeypatch.setattr(session_start.github, "gh_auth_ok", lambda: False)
    rc = session_start.main()
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    latency = [l for l in lines if l["metric"] == "latency"]
    assert len(latency) == 1
    assert latency[0]["hook"] == "session_start"
    assert isinstance(latency[0]["duration_ms"], int)
