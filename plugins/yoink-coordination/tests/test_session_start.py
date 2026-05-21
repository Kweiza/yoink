"""SessionStart tests after v0.3.15.

Self-heal removed (heartbeat-based eviction conflicted with the
"task lives until primary-merge" rule). task_cache.clear removed (stamp
follows task lifetime, not session lifetime). SessionStart now only
emits latency and prints peer activity.

v0.3.29: SessionStart also cleans my own past session entries whose
remote branch has been deleted (Task 5 — branch-level only).
"""
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))


class _NullLockSS:
    def __enter__(self): return self
    def __exit__(self, *exc): return False


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
@patch("github.list_my_status_issues", return_value=[])
@patch("context.build_context")
def test_session_start_does_not_touch_my_issue(bc, my_issues, other, addlbl, create, edit, auth, labels, tmp_path, monkeypatch):
    """v0.3.15: SessionStart never creates an issue, never edits a body,
    never adds a label. Lazy creation and all body management belong to
    PreToolUse / stop.py.

    v0.3.29: with no existing yoink:status issue, the stale-entry sweep
    is a no-op (nothing to iterate)."""
    monkeypatch.chdir(tmp_path)
    from context import Context
    bc.return_value = Context("alice", "o/r", "main", str(tmp_path),
                              None, "uuid-1", None, "2026-04-15T10:00:00Z")
    with patch("session_start.lock.acquire",
               side_effect=lambda *a, **k: _NullLockSS()):
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


# ---------------------------------------------------------------------------
# v0.3.29 Task 5: stale-branch sweep
# ---------------------------------------------------------------------------


@patch("github.close_issue", return_value=True)
@patch("github.remove_label", return_value=True)
@patch("github.edit_issue_body", return_value=True)
@patch("github.list_my_status_issues")
@patch("github.list_other_status_issues_open", return_value=[])
@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("context.build_context")
def test_session_start_cleans_deleted_branch_entries(
    bc, auth, labels, others, my_issues, edit, rm_label, close,
    tmp_path, monkeypatch,
):
    """When a past session's branch no longer exists on remote,
    SessionStart drops that session entry."""
    monkeypatch.chdir(tmp_path)
    from context import Context
    import state as state_mod

    bc.return_value = Context("alice", "o/r", "main", str(tmp_path),
                              None, "uuid-new", None, "2026-04-16T10:00:00Z")

    old_session = state_mod.Session(
        session_id="old-s", worktree_path=str(tmp_path), branch="deleted-branch",
        task_issue=None,
        started_at="2026-04-15T00:00:00Z",
        declared_files=[{"path": "x.txt", "declared_at": "2026-04-15T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="old-ccs",
    )
    parsed = state_mod.State(updated_at="", sessions=[old_session])
    body = state_mod.render_body(parsed, login="alice")
    my_issues.return_value = [{"number": 5, "state": "OPEN", "body": body,
                               "assignees": [{"login": "alice"}]}]

    writes = []
    edit.side_effect = lambda num, body: writes.append(body) or True

    with patch("session_start.gitops.remote_branch_exists", return_value=False), \
         patch("session_start.gitops.git_repo_healthy", return_value=True), \
         patch("session_start.lock.acquire",
               side_effect=lambda *a, **k: _NullLockSS()):
        import session_start
        rc = session_start.main()

    assert rc == 0
    assert len(writes) == 1
    # The deleted-branch session should be gone
    assert "deleted-branch" not in writes[0]
    # All sessions dropped → close
    assert close.called
    assert rm_label.called


@patch("github.close_issue", return_value=True)
@patch("github.remove_label", return_value=True)
@patch("github.edit_issue_body", return_value=True)
@patch("github.list_my_status_issues")
@patch("github.list_other_status_issues_open", return_value=[])
@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("context.build_context")
def test_session_start_keeps_entries_with_existing_branch(
    bc, auth, labels, others, my_issues, edit, rm_label, close,
    tmp_path, monkeypatch,
):
    """If the remote branch still exists, don't touch the entry."""
    monkeypatch.chdir(tmp_path)
    from context import Context
    import state as state_mod

    bc.return_value = Context("alice", "o/r", "main", str(tmp_path),
                              None, "uuid-new", None, "2026-04-16T10:00:00Z")

    live_session = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="feat",
        task_issue=None,
        started_at="2026-04-15T00:00:00Z",
        declared_files=[{"path": "x.txt", "declared_at": "2026-04-15T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="ccs",
    )
    parsed = state_mod.State(updated_at="", sessions=[live_session])
    body = state_mod.render_body(parsed, login="alice")
    my_issues.return_value = [{"number": 5, "state": "OPEN", "body": body,
                               "assignees": [{"login": "alice"}]}]

    with patch("session_start.gitops.remote_branch_exists", return_value=True), \
         patch("session_start.gitops.git_repo_healthy", return_value=True), \
         patch("session_start.lock.acquire",
               side_effect=lambda *a, **k: _NullLockSS()):
        import session_start
        rc = session_start.main()

    assert rc == 0
    assert not edit.called
    assert not close.called


@patch("github.close_issue", return_value=True)
@patch("github.remove_label", return_value=True)
@patch("github.edit_issue_body", return_value=True)
@patch("github.list_my_status_issues")
@patch("github.list_other_status_issues_open", return_value=[])
@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("context.build_context")
def test_session_start_preserves_primary_branch_sessions(
    bc, auth, labels, others, my_issues, edit, rm_label, close,
    tmp_path, monkeypatch,
):
    """Sessions on main/master are never dropped (primary isn't branch-deletable)."""
    monkeypatch.chdir(tmp_path)
    from context import Context
    import state as state_mod

    bc.return_value = Context("alice", "o/r", "main", str(tmp_path),
                              None, "uuid-new", None, "2026-04-16T10:00:00Z")

    main_session = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="main",
        task_issue=None,
        started_at="2026-04-15T00:00:00Z",
        declared_files=[{"path": "x.txt", "declared_at": "2026-04-15T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="ccs",
    )
    parsed = state_mod.State(updated_at="", sessions=[main_session])
    body = state_mod.render_body(parsed, login="alice")
    my_issues.return_value = [{"number": 5, "state": "OPEN", "body": body,
                               "assignees": [{"login": "alice"}]}]

    # Even if remote_branch_exists returned False (shouldn't for main, but defensive),
    # main sessions should be preserved.
    with patch("session_start.gitops.remote_branch_exists", return_value=False), \
         patch("session_start.gitops.git_repo_healthy", return_value=True), \
         patch("session_start.lock.acquire",
               side_effect=lambda *a, **k: _NullLockSS()):
        import session_start
        rc = session_start.main()

    assert rc == 0
    assert not edit.called  # main session preserved


@patch("github.close_issue", return_value=True)
@patch("github.edit_issue_body", return_value=True)
@patch("github.list_my_status_issues")
@patch("github.list_other_status_issues_open", return_value=[])
@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("context.build_context")
def test_session_start_skips_closed_primary(
    bc, auth, labels, others, my_issues, edit, close,
    tmp_path, monkeypatch,
):
    """If my primary yoink:status issue is already closed, no cleanup attempt."""
    monkeypatch.chdir(tmp_path)
    from context import Context
    bc.return_value = Context("alice", "o/r", "main", str(tmp_path),
                              None, "uuid-new", None, "2026-04-16T10:00:00Z")
    my_issues.return_value = [{"number": 5, "state": "CLOSED", "body": "",
                               "assignees": [{"login": "alice"}]}]

    with patch("session_start.lock.acquire",
               side_effect=lambda *a, **k: _NullLockSS()):
        import session_start
        rc = session_start.main()

    assert rc == 0
    assert not edit.called


@patch("github.close_issue", return_value=True)
@patch("github.remove_label", return_value=True)
@patch("github.edit_issue_body", return_value=True)
@patch("github.list_my_status_issues")
@patch("github.list_other_status_issues_open", return_value=[])
@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("context.build_context")
def test_session_start_uses_session_worktree_for_branch_check(
    bc, auth, labels, others, my_issues, edit, rm_label, close,
    tmp_path, monkeypatch,
):
    """When a session's own worktree_path still exists, the branch-check
    subprocess should run against that path (not current ctx.worktree_path).
    """
    monkeypatch.chdir(tmp_path)
    from context import Context
    import state as state_mod

    current_wt = tmp_path / "current"
    past_wt = tmp_path / "past"
    current_wt.mkdir()
    past_wt.mkdir()

    bc.return_value = Context("alice", "o/r", "main", str(current_wt),
                              None, "uuid-new", None, "2026-04-16T10:00:00Z")

    past_session = state_mod.Session(
        session_id="s", worktree_path=str(past_wt), branch="feat-past",
        task_issue=None,
        started_at="2026-04-15T00:00:00Z",
        declared_files=[{"path": "x.txt", "declared_at": "2026-04-15T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="ccs",
    )
    parsed = state_mod.State(updated_at="", sessions=[past_session])
    body = state_mod.render_body(parsed, login="alice")
    my_issues.return_value = [{"number": 5, "state": "OPEN", "body": body,
                               "assignees": [{"login": "alice"}]}]

    branch_check_calls = []

    def fake_remote_exists(cwd, branch):
        branch_check_calls.append((str(cwd), branch))
        return True  # Branch exists — keep the entry

    with patch("session_start.gitops.remote_branch_exists",
               side_effect=fake_remote_exists), \
         patch("session_start.gitops.git_repo_healthy", return_value=True), \
         patch("session_start.lock.acquire",
               side_effect=lambda *a, **k: _NullLockSS()):
        import session_start
        rc = session_start.main()

    assert rc == 0
    assert len(branch_check_calls) == 1
    called_cwd, called_branch = branch_check_calls[0]
    assert called_cwd == str(past_wt), f"expected past_wt, got {called_cwd}"
    assert called_branch == "feat-past"


@patch("github.close_issue", return_value=True)
@patch("github.edit_issue_body", return_value=True)
@patch("github.list_my_status_issues")
@patch("github.list_other_status_issues_open", return_value=[])
@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("context.build_context")
def test_session_start_skips_corrupt_body(
    bc, auth, labels, others, my_issues, edit, close,
    tmp_path, monkeypatch,
):
    """If the body can't be parsed, don't edit anything."""
    monkeypatch.chdir(tmp_path)
    from context import Context
    bc.return_value = Context("alice", "o/r", "main", str(tmp_path),
                              None, "uuid-new", None, "2026-04-16T10:00:00Z")
    # Body with markers but broken JSON → parse_body sets corrupt=True
    corrupt_body = (
        "<!-- yoink:state-json-v1:begin\n"
        "{bad json\n"
        "yoink:state-json-v1:end -->"
    )
    my_issues.return_value = [{"number": 5, "state": "OPEN", "body": corrupt_body,
                               "assignees": [{"login": "alice"}]}]

    with patch("session_start.lock.acquire",
               side_effect=lambda *a, **k: _NullLockSS()):
        import session_start
        rc = session_start.main()

    assert rc == 0
    assert not edit.called
