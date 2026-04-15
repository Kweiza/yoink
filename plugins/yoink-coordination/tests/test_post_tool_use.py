import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# conftest adds lib/; we additionally need hooks/
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))

import post_tool_use as hook  # noqa


def _inp(command="git commit -m wip", interrupted=False, stdout="", stderr=""):
    # Task 0 E: tool_response has no exit_code field; interrupted/stdout/stderr instead.
    return json.dumps({
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "session_id": "test-session-id",
        "tool_input": {"command": command},
        "tool_response": {"interrupted": interrupted, "stdout": stdout, "stderr": stderr,
                          "isImage": False, "noOutputExpected": False},
    })


def test_non_commit_command_noop():
    assert hook.run(stdin_text=_inp(command="ls")) == 0


def test_commit_interrupted_noop():
    # User Ctrl+C during commit → skip release path
    assert hook.run(stdin_text=_inp(command="git commit -m wip", interrupted=True)) == 0


def test_commit_success_releases_only_committed_paths(tmp_path):
    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="b", worktree_path="/tmp/wt",
        session_id="s", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-14T10:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink", lock_timeout_seconds=10
    )

    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_committed", return_value={"a.py"}), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config", return_value=(fake_cfg, [])), \
         patch.object(hook, "_apply_release") as ap:
        hook.run(stdin_text=_inp())
        ap.assert_called_once()
        # called with (ctx, cfg, project_dir, hook_session_id, committed)
        args, _ = ap.call_args
        assert args[3] == "test-session-id"   # Task 0 A session_id is wired to _apply_release
        assert args[-1] == {"a.py"}


def test_commit_success_updates_last_heartbeat(tmp_path):
    """When release writes the body, last_heartbeat must be bumped to now."""
    import state as state_mod

    me = state_mod.Session(
        session_id="s", worktree_path="/w", branch="main",
        task_issue=None,
        started_at="2026-04-14T00:00:00Z",
        last_heartbeat="2026-04-14T00:00:00Z",
        declared_files=[{"path": "a.py", "declared_at": "2026-04-14T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="test-session-id",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    writes = []
    def capture_edit(num, body):
        writes.append(body)
        return True

    from types import SimpleNamespace
    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="main", worktree_path="/w",
        session_id="s", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-14T00:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
        heartbeat_cooldown_seconds=120, stale_threshold_seconds=900,
    )

    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_committed", return_value={"a.py"}), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config", return_value=(fake_cfg, [])), \
         patch.object(hook.github, "list_my_status_issues",
                      return_value=[{"number": 1, "state": "OPEN", "body": existing_body,
                                     "assignees": [{"login": "kweiza"}]}]), \
         patch.object(hook.github, "edit_issue_body", side_effect=capture_edit), \
         patch.object(hook.lock, "acquire") as lock_mock:
        # Null-lock context manager
        lock_mock.return_value.__enter__ = lambda self: None
        lock_mock.return_value.__exit__ = lambda self, *a: False

        hook.run(stdin_text=_inp())

    assert len(writes) == 1
    body_out = writes[0]
    # The stale 2026-04-14T00:00:00Z heartbeat must no longer be the latest one
    # written; it should have been replaced with a current ISO timestamp.
    assert '"last_heartbeat": "2026-04-14T00:00:00Z"' not in body_out
    assert '"declared_files": []' in body_out  # release removed a.py


# ------------------------------------------------------------------
# Phase 5 telemetry: latency always; release_applied / release_skipped
# ------------------------------------------------------------------
import re
import json as _json


def _metric_lines(err: str) -> list:
    out = []
    for ln in err.splitlines():
        m = re.match(r"\[yoink-metric\] (\{.*\})$", ln)
        if m:
            out.append(_json.loads(m.group(1)))
    return out


def test_post_tool_use_emits_latency_on_non_bash_early_return(capsys):
    rc = hook.run(stdin_text=_json.dumps({"tool_name": "Edit"}))
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    latency = [l for l in lines if l["metric"] == "latency"]
    assert len(latency) == 1
    assert latency[0]["hook"] == "post_tool_use"


def test_post_tool_use_emits_release_skipped_committed_empty(tmp_path, capsys):
    """`_committed()` returns empty set. Expect release_skipped reason=committed_empty."""
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_committed", return_value=set()):
        rc = hook.run(stdin_text=_inp())
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    skipped = [l for l in lines if l["metric"] == "release_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "committed_empty"


def test_post_tool_use_emits_release_skipped_no_session(tmp_path, capsys):
    """`_apply_release` iterates issue sessions but none match my ccs/worktree."""
    import state as state_mod
    unrelated = state_mod.Session(
        session_id="other", worktree_path="/not-my-wt", branch="other",
        task_issue=None, started_at="2026-04-14T10:00:00Z",
        last_heartbeat="2026-04-14T10:00:00Z",
        declared_files=[{"path": "x.py", "declared_at": "2026-04-14T10:00:00Z"}],
        driven_by="claude-code", claude_session_id="ccs-other",
    )
    parsed = state_mod.State(updated_at="", sessions=[unrelated])
    body = state_mod.render_body(parsed, login="kweiza")
    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="b", worktree_path="/tmp/wt",
        session_id="s", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-14T10:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink", lock_timeout_seconds=10,
    )
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_committed", return_value={"x.py"}), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config", return_value=(fake_cfg, [])), \
         patch("post_tool_use.github.list_my_status_issues",
               return_value=[{"number": 1, "body": body,
                              "assignees": [{"login": "kweiza"}]}]), \
         patch("post_tool_use.github.edit_issue_body", return_value=True), \
         patch("post_tool_use.lock.acquire",
               side_effect=lambda *a, **k: _NullLockPTU()):
        rc = hook.run(stdin_text=_inp())
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    skipped = [l for l in lines if l["metric"] == "release_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "no_session"


def test_post_tool_use_emits_release_skipped_no_declared(tmp_path, capsys):
    """Matched session has empty declared_files → release_skipped reason=no_declared."""
    import state as state_mod
    mine = state_mod.Session(
        session_id="s", worktree_path="/tmp/wt", branch="b",
        task_issue=None, started_at="2026-04-14T10:00:00Z",
        last_heartbeat="2026-04-14T10:00:00Z", declared_files=[],
        driven_by="claude-code", claude_session_id="test-session-id",
    )
    parsed = state_mod.State(updated_at="", sessions=[mine])
    body = state_mod.render_body(parsed, login="kweiza")
    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="b", worktree_path="/tmp/wt",
        session_id="s", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-14T10:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink", lock_timeout_seconds=10,
    )
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_committed", return_value={"x.py"}), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config", return_value=(fake_cfg, [])), \
         patch("post_tool_use.github.list_my_status_issues",
               return_value=[{"number": 1, "body": body,
                              "assignees": [{"login": "kweiza"}]}]), \
         patch("post_tool_use.github.edit_issue_body", return_value=True), \
         patch("post_tool_use.lock.acquire",
               side_effect=lambda *a, **k: _NullLockPTU()):
        rc = hook.run(stdin_text=_inp())
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    skipped = [l for l in lines if l["metric"] == "release_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "no_declared"


def test_post_tool_use_emits_release_applied_with_counts(tmp_path, capsys):
    """Matched session with declared_files intersecting committed → release_applied."""
    import state as state_mod
    mine = state_mod.Session(
        session_id="s", worktree_path="/tmp/wt", branch="b",
        task_issue=None, started_at="2026-04-14T10:00:00Z",
        last_heartbeat="2026-04-14T10:00:00Z",
        declared_files=[{"path": "a.py", "declared_at": "2026-04-14T10:00:00Z"},
                        {"path": "b.py", "declared_at": "2026-04-14T10:00:00Z"}],
        driven_by="claude-code", claude_session_id="test-session-id",
    )
    parsed = state_mod.State(updated_at="", sessions=[mine])
    body = state_mod.render_body(parsed, login="kweiza")
    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="b", worktree_path="/tmp/wt",
        session_id="s", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-14T10:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink", lock_timeout_seconds=10,
    )
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_committed", return_value={"a.py"}), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config", return_value=(fake_cfg, [])), \
         patch("post_tool_use.github.list_my_status_issues",
               return_value=[{"number": 1, "body": body,
                              "assignees": [{"login": "kweiza"}]}]), \
         patch("post_tool_use.github.edit_issue_body", return_value=True), \
         patch("post_tool_use.lock.acquire",
               side_effect=lambda *a, **k: _NullLockPTU()):
        rc = hook.run(stdin_text=_inp())
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    applied = [l for l in lines if l["metric"] == "release_applied"]
    assert len(applied) == 1
    assert applied[0]["committed_count"] == 1
    assert applied[0]["declared_before_count"] == 2
    assert applied[0]["removed_count"] == 1
    assert applied[0]["matched_session"] is True


class _NullLockPTU:
    """Minimal context-manager stub for replacing lock.acquire in tests above."""
    def __enter__(self): return self
    def __exit__(self, *exc): return False
