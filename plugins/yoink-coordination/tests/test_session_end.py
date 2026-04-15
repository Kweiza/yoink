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
