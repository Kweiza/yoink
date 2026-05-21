"""Tests for hooks/post_tool_use.py (v0.3.29+).

Heartbeat logic was retired in v0.3.28. Starting v0.3.29 this hook:
- is a pure noop for `git commit` (latency still emitted via LatencyTimer);
- detects git revert-like commands (checkout, restore, reset --hard, switch)
  and sweeps my declared_files, dropping entries whose state reverted to
  base (working-tree clear AND not in branch diff vs main).
"""
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))

import post_tool_use as hook  # noqa


def _inp(command="git commit -m wip", interrupted=False, stdout="", stderr=""):
    return json.dumps({
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "session_id": "test-session-id",
        "tool_input": {"command": command},
        "tool_response": {"interrupted": interrupted, "stdout": stdout,
                          "stderr": stderr,
                          "isImage": False, "noOutputExpected": False},
    })


def _metric_lines(err: str) -> list:
    out = []
    for ln in err.splitlines():
        m = re.match(r"\[yoink-metric\] (\{.*\})$", ln)
        if m:
            out.append(json.loads(m.group(1)))
    return out


class _NullLockPTU:
    def __enter__(self): return self
    def __exit__(self, *exc): return False


def test_non_commit_command_noop():
    assert hook.run(stdin_text=_inp(command="ls")) == 0


def test_commit_interrupted_noop():
    assert hook.run(stdin_text=_inp(command="git commit -m wip",
                                    interrupted=True)) == 0


def test_post_tool_use_emits_latency_on_non_bash_early_return(capsys):
    rc = hook.run(stdin_text=json.dumps({"tool_name": "Edit"}))
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    latency = [l for l in lines if l["metric"] == "latency"]
    assert len(latency) == 1
    assert latency[0]["hook"] == "post_tool_use"


def test_commit_command_noop_no_write(tmp_path):
    """v0.3.29: git commit is pure noop. Must NOT even call gh."""
    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="main", worktree_path="/w",
        session_id="s", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-14T00:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
    )
    gh_calls = []
    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config", return_value=(fake_cfg, [])), \
         patch("post_tool_use.github.list_my_status_issues",
               side_effect=lambda *a, **k: gh_calls.append(a) or []), \
         patch("post_tool_use.github.edit_issue_body",
               side_effect=lambda num, body: writes.append(body) or True), \
         patch("post_tool_use.lock.acquire",
               side_effect=lambda *a, **k: _NullLockPTU()):
        hook.run(stdin_text=_inp(command="git commit -m wip"))
    assert writes == []
    assert gh_calls == []


def test_revert_command_removes_unclaimed_declared_files(tmp_path):
    """git restore/checkout → unclaimed files removed from declared_files."""
    import state as state_mod

    me = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="feat",
        task_issue=None,
        started_at="2026-04-16T00:00:00Z",
        declared_files=[
            {"path": "a.txt", "declared_at": "2026-04-16T00:00:00Z"},
            {"path": "b.txt", "declared_at": "2026-04-16T00:00:00Z"},
        ],
        driven_by="claude-code",
        claude_session_id="test-session-id",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="feat", worktree_path=str(tmp_path),
        session_id="s", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-16T00:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
    )

    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config", return_value=(fake_cfg, [])), \
         patch("post_tool_use.github.list_my_status_issues",
               return_value=[{"number": 1, "state": "OPEN",
                              "body": existing_body,
                              "assignees": [{"login": "kweiza"}]}]), \
         patch("post_tool_use.github.edit_issue_body",
               side_effect=lambda num, body: writes.append(body) or True), \
         patch("post_tool_use.github.close_issue", return_value=True), \
         patch("post_tool_use.github.remove_label", return_value=True), \
         patch("post_tool_use.lock.acquire",
               side_effect=lambda *a, **k: _NullLockPTU()), \
         patch("post_tool_use.gitops.working_tree_paths",
               return_value={"b.txt"}), \
         patch("post_tool_use.gitops.branch_diff_paths",
               return_value=set()):
        hook.run(stdin_text=_inp(command="git checkout -- a.txt"))

    assert len(writes) == 1
    assert "b.txt" in writes[0]
    # a.txt should be gone from the JSON block
    assert '"path": "a.txt"' not in writes[0]


def test_revert_removes_session_when_all_files_unclaimed(tmp_path):
    """When all declared_files become unclaimed, session entry dropped
    and issue closed."""
    import state as state_mod

    me = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="feat",
        task_issue=None,
        started_at="2026-04-16T00:00:00Z",
        declared_files=[{"path": "a.txt", "declared_at": "2026-04-16T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="test-session-id",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="feat", worktree_path=str(tmp_path),
        session_id="s", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-16T00:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
    )

    closed = []
    removed_labels = []
    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config", return_value=(fake_cfg, [])), \
         patch("post_tool_use.github.list_my_status_issues",
               return_value=[{"number": 1, "state": "OPEN",
                              "body": existing_body,
                              "assignees": [{"login": "kweiza"}]}]), \
         patch("post_tool_use.github.edit_issue_body",
               side_effect=lambda num, body: writes.append(body) or True), \
         patch("post_tool_use.github.close_issue",
               side_effect=lambda num: closed.append(num) or True), \
         patch("post_tool_use.github.remove_label",
               side_effect=lambda num, lab: removed_labels.append((num, lab)) or True), \
         patch("post_tool_use.lock.acquire",
               side_effect=lambda *a, **k: _NullLockPTU()), \
         patch("post_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("post_tool_use.gitops.branch_diff_paths", return_value=set()):
        hook.run(stdin_text=_inp(command="git restore a.txt"))

    assert closed == [1]
    assert removed_labels == [(1, "yoink:active")]


def test_revert_fail_open_when_working_tree_unreadable(tmp_path):
    """If gitops.working_tree_paths returns None, don't release anything."""
    import state as state_mod

    me = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="feat",
        task_issue=None,
        started_at="2026-04-16T00:00:00Z",
        declared_files=[{"path": "a.txt", "declared_at": "2026-04-16T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="test-session-id",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="feat", worktree_path=str(tmp_path),
        session_id="s", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-16T00:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
    )

    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config", return_value=(fake_cfg, [])), \
         patch("post_tool_use.github.list_my_status_issues",
               return_value=[{"number": 1, "state": "OPEN",
                              "body": existing_body,
                              "assignees": [{"login": "kweiza"}]}]), \
         patch("post_tool_use.github.edit_issue_body",
               side_effect=lambda num, body: writes.append(body) or True), \
         patch("post_tool_use.lock.acquire",
               side_effect=lambda *a, **k: _NullLockPTU()), \
         patch("post_tool_use.gitops.working_tree_paths", return_value=None), \
         patch("post_tool_use.gitops.branch_diff_paths", return_value=set()):
        hook.run(stdin_text=_inp(command="git checkout -- a.txt"))

    # Fail-open: no body write when working tree unreadable
    assert writes == []


def test_revert_interrupted_noop():
    """Interrupted bash command → no cleanup."""
    assert hook.run(stdin_text=_inp(command="git restore file.txt",
                                    interrupted=True)) == 0


def test_revert_does_not_touch_other_session(tmp_path):
    """Revert in my current session must not release claims of my
    OTHER active sessions (e.g., sibling worktree)."""
    import state as state_mod

    me = state_mod.Session(
        session_id="s-current", worktree_path=str(tmp_path), branch="feat-a",
        task_issue=None,
        started_at="2026-04-16T00:00:00Z",
        declared_files=[{"path": "a.txt", "declared_at": "2026-04-16T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="test-session-id",
    )
    other = state_mod.Session(
        session_id="s-other", worktree_path="/other-wt", branch="feat-b",
        task_issue=None,
        started_at="2026-04-16T00:00:00Z",
        declared_files=[{"path": "b.txt", "declared_at": "2026-04-16T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="other-ccs",
    )
    parsed = state_mod.State(updated_at="", sessions=[me, other])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="feat-a", worktree_path=str(tmp_path),
        session_id="s-current", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-16T00:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
    )

    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config", return_value=(fake_cfg, [])), \
         patch("post_tool_use.github.list_my_status_issues",
               return_value=[{"number": 1, "state": "OPEN",
                              "body": existing_body,
                              "assignees": [{"login": "kweiza"}]}]), \
         patch("post_tool_use.github.edit_issue_body",
               side_effect=lambda num, body: writes.append(body) or True), \
         patch("post_tool_use.lock.acquire",
               side_effect=lambda *a, **k: _NullLockPTU()), \
         patch("post_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("post_tool_use.gitops.branch_diff_paths", return_value=set()):
        hook.run(stdin_text=_inp(command="git checkout -- a.txt"))

    assert len(writes) == 1
    # My session's a.txt is gone
    assert '"path": "a.txt"' not in writes[0]
    # But the other session's b.txt is preserved
    assert '"path": "b.txt"' in writes[0]


def test_revert_skips_closed_issue(tmp_path):
    """A CLOSED primary issue must not be edited (no ghost writes)."""
    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="feat", worktree_path=str(tmp_path),
        session_id="s", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-16T00:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
    )
    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config", return_value=(fake_cfg, [])), \
         patch("post_tool_use.github.list_my_status_issues",
               return_value=[{"number": 1, "state": "CLOSED", "body": "",
                              "assignees": [{"login": "kweiza"}]}]), \
         patch("post_tool_use.github.edit_issue_body",
               side_effect=lambda num, body: writes.append(body) or True), \
         patch("post_tool_use.lock.acquire",
               side_effect=lambda *a, **k: _NullLockPTU()):
        hook.run(stdin_text=_inp(command="git checkout -- a.txt"))
    assert writes == []
