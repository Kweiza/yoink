import sys
from unittest.mock import patch
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

from state import State, Session, render_body

def _ctx(cc="ccs-1"):
    from context import Context
    return Context("alice", "o/r", "main", "/ws", None, "uuid-new", cc, "2026-04-14T10:00:00Z")

@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("github.list_my_status_issues")
@patch("github.edit_issue_body", return_value=True)
@patch("github.remove_label", return_value=True)
@patch("github.close_issue", return_value=True)
@patch("context.build_context")
def test_end_last_session_closes_issue(bc, close, remove, edit, my, auth, labels, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bc.return_value = _ctx()
    body = render_body(State("t", [Session("old","/ws","main",None,"t","t",[],"claude-code","ccs-1")]), login="alice")
    my.return_value = [{"number": 5, "state": "OPEN", "body": body, "assignees": [{"login": "alice"}]}]
    import session_end
    assert session_end.main() == 0
    close.assert_called_once_with(5)
    remove.assert_called_with(5, "yoink:active")

@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("github.list_my_status_issues")
@patch("github.edit_issue_body", return_value=True)
@patch("github.close_issue", return_value=True)
@patch("context.build_context")
def test_end_with_other_session_left_keeps_open(bc, close, edit, my, auth, labels, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bc.return_value = _ctx()
    body = render_body(State("t", [
        Session("old","/ws","main",None,"t","t",[],"claude-code","ccs-1"),
        Session("other","/ws2","dev",None,"t","t",[],"claude-code","ccs-2"),
    ]), login="alice")
    my.return_value = [{"number": 5, "state": "OPEN", "body": body, "assignees": [{"login": "alice"}]}]
    import session_end
    assert session_end.main() == 0
    close.assert_not_called()
    edit.assert_called_once()

@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("github.list_my_status_issues")
@patch("github.edit_issue_body", return_value=True)
@patch("github.remove_label", return_value=True)
@patch("github.close_issue", return_value=True)
@patch("context.build_context")
def test_end_without_ccs_in_ctx_still_removes_session_by_wb(
    bc, close, remove, edit, my, auth, labels, monkeypatch, tmp_path
):
    """Reproduces Task 14 bug: Claude Code v2.1.105 does not set CLAUDE_ENV_FILE on
    SessionEnd, so ctx.claude_session_id is None. SessionEnd must still remove the
    session by matching on (worktree, branch)."""
    monkeypatch.chdir(tmp_path)
    from context import Context
    # Ctx has NO claude_session_id (the actual Claude Code v2.1.105 SessionEnd reality)
    bc.return_value = Context("alice", "o/r", "main", "/ws", None, "uuid-new",
                              None, "2026-04-14T10:00:00Z")
    # Stored session DID have a ccs (set by SessionStart earlier)
    body = render_body(State("t", [
        Session("old", "/ws", "main", None, "t", "t", [], "claude-code", "ccs-from-start"),
    ]), login="alice")
    my.return_value = [{"number": 5, "state": "OPEN", "body": body,
                        "assignees": [{"login": "alice"}]}]
    import session_end
    assert session_end.main() == 0
    close.assert_called_once_with(5)

@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("github.list_my_status_issues")
@patch("github.edit_issue_body", return_value=True)
@patch("github.close_issue", return_value=True)
@patch("context.build_context")
def test_end_with_ccs_match_beats_wb_ambiguity(
    bc, close, edit, my, auth, labels, monkeypatch, tmp_path
):
    """When two sessions share wb but have different ccs, and ctx has ccs, only the
    ccs-matching session is removed."""
    monkeypatch.chdir(tmp_path)
    from context import Context
    bc.return_value = Context("alice", "o/r", "main", "/ws", None, "uuid-new",
                              "ccs-A", "2026-04-14T10:00:00Z")
    body = render_body(State("t", [
        Session("a", "/ws", "main", None, "t", "t", [], "claude-code", "ccs-A"),
        Session("b", "/ws", "main", None, "t", "t", [], "claude-code", "ccs-B"),
    ]), login="alice")
    my.return_value = [{"number": 5, "state": "OPEN", "body": body,
                        "assignees": [{"login": "alice"}]}]
    import session_end
    import importlib; importlib.reload(session_end)
    assert session_end.main() == 0
    close.assert_not_called()  # one session remains
    edit.assert_called_once()

import re
import json as _json

def test_session_end_emits_latency_on_main(capsys, monkeypatch):
    import session_end
    monkeypatch.setattr(session_end.github, "gh_auth_ok", lambda: False)
    rc = session_end.main()
    assert rc == 0
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if ln.startswith("[yoink-metric] ")]
    parsed = [_json.loads(ln.split(" ", 1)[1]) for ln in lines]
    latency = [p for p in parsed if p["metric"] == "latency"]
    assert len(latency) == 1
    assert latency[0]["hook"] == "session_end"


def test_session_end_clears_task_stamp(tmp_path, monkeypatch):
    """v0.3.12: SessionEnd drops the stamp so the next session starts
    with a fresh prompt."""
    import importlib, sys as _sys
    from pathlib import Path as _Path
    hooks = _Path(__file__).resolve().parents[1] / "hooks"
    if str(hooks) not in _sys.path:
        _sys.path.insert(0, str(hooks))
    monkeypatch.setenv("YOINK_TASK_CACHE_ROOT", str(tmp_path / "cache"))
    import task_cache as tc
    importlib.reload(tc)
    import session_end as hook
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
    monkeypatch.setattr(hook.github, "gh_auth_ok", lambda: True)
    monkeypatch.setattr(hook.github, "label_exists", lambda l: False)
    monkeypatch.setattr(hook.ctx_mod, "build_context", lambda: fake_ctx)
    monkeypatch.setattr(
        hook.cfg_mod, "load_config",
        lambda d: (SimpleNamespace(label_prefix="yoink",
                                    lock_timeout_seconds=10), []),
    )
    rc = hook.main()
    assert rc == 0
    assert hook.task_cache.is_set("/wt", "main") is False
