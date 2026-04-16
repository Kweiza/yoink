"""Tests for hooks/post_tool_use.py (v0.3.7+).

Release of declared_files moved to Stop hook (merge-based). PostToolUse now
only bumps last_heartbeat on detected git commits so long commit sequences
keep the session fresh.
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


def test_commit_success_bumps_heartbeat_on_matched_session(tmp_path):
    """When a git commit is detected and the session matches, last_heartbeat
    advances and the issue body is written."""
    import state as state_mod

    me = state_mod.Session(
        session_id="s", worktree_path="/w", branch="main",
        task_issue=None,
        started_at="2026-04-14T00:00:00Z",
        declared_files=[],
        driven_by="claude-code",
        claude_session_id="test-session-id",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    existing_body = state_mod.render_body(parsed, login="kweiza")

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

    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config",
               return_value=(fake_cfg, [])), \
         patch("post_tool_use.github.list_my_status_issues",
               return_value=[{"number": 1, "state": "OPEN",
                              "body": existing_body,
                              "assignees": [{"login": "kweiza"}]}]), \
         patch("post_tool_use.github.edit_issue_body",
               side_effect=lambda num, body: writes.append(body) or True), \
         patch("post_tool_use.lock.acquire",
               side_effect=lambda *a, **k: _NullLockPTU()):
        hook.run(stdin_text=_inp())

    assert len(writes) == 1
    # The stale 2026-04-14T00:00:00Z heartbeat must no longer be present as
    # the session's recorded heartbeat.
    assert '"last_heartbeat": "2026-04-14T00:00:00Z"' not in writes[0]


def test_commit_success_skips_write_when_no_matching_session(tmp_path):
    """If the issue body has no session matching my ccs/worktree, no write."""
    import state as state_mod
    unrelated = state_mod.Session(
        session_id="other", worktree_path="/other", branch="other",
        task_issue=None,
        started_at="2026-04-14T10:00:00Z",
        declared_files=[],
        driven_by="claude-code", claude_session_id="ccs-other",
    )
    parsed = state_mod.State(updated_at="", sessions=[unrelated])
    body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="main", worktree_path="/w",
        session_id="s", claude_session_id="test-session-id",
        task_issue=None, started_at="2026-04-14T10:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
    )

    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch("post_tool_use.ctx_mod.build_context", return_value=fake_ctx), \
         patch("post_tool_use.cfg_mod.load_config",
               return_value=(fake_cfg, [])), \
         patch("post_tool_use.github.list_my_status_issues",
               return_value=[{"number": 1, "state": "OPEN", "body": body,
                              "assignees": [{"login": "kweiza"}]}]), \
         patch("post_tool_use.github.edit_issue_body",
               side_effect=lambda num, body: writes.append(body) or True), \
         patch("post_tool_use.lock.acquire",
               side_effect=lambda *a, **k: _NullLockPTU()):
        hook.run(stdin_text=_inp())

    assert writes == []
