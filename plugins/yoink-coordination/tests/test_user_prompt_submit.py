"""Tests for hooks/user_prompt_submit.py (v0.3.8+)."""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))

import user_prompt_submit as hook  # noqa


def _stdin(session_id="s-1"):
    return json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "session_id": session_id,
        "prompt": "hi",
    })


def _metric_lines(err: str) -> list:
    out = []
    for ln in err.splitlines():
        m = re.match(r"\[yoink-metric\] (\{.*\})$", ln)
        if m:
            out.append(json.loads(m.group(1)))
    return out


def test_latency_emitted_on_early_return(capsys, monkeypatch):
    """No CLAUDE_PROJECT_DIR → early return. Latency still emits."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert hook.run(stdin_text=_stdin()) == 0
    lines = _metric_lines(capsys.readouterr().err)
    latency = [l for l in lines if l["metric"] == "latency"]
    assert len(latency) == 1
    assert latency[0]["hook"] == "user_prompt_submit"


def test_reminder_printed_when_task_summary_missing(capsys, monkeypatch, tmp_path):
    """Session entry lacks task_summary → stdout reminder printed."""
    import state as state_mod

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    me = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="main",
        task_issue=None, started_at="2026-04-15T00:00:00Z",
        last_heartbeat="2026-04-15T00:00:00Z",
        declared_files=[], driven_by="claude-code",
        claude_session_id="s-1",
        task_summary=None,
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="main", worktree_path=str(tmp_path),
        session_id="s", claude_session_id="s-1",
        task_issue=None, started_at="2026-04-15T00:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
        heartbeat_cooldown_seconds=120, stale_threshold_seconds=900,
        primary_branch=None,
    )

    with patch.object(hook.github, "gh_auth_ok", return_value=True), \
         patch.object(hook.ctx_mod, "build_context", return_value=fake_ctx), \
         patch.object(hook.cfg_mod, "load_config", return_value=(fake_cfg, [])), \
         patch.object(hook.github, "list_my_status_issues",
                      return_value=[{"number": 1, "state": "OPEN", "body": body,
                                     "assignees": [{"login": "kweiza"}]}]):
        assert hook.run(stdin_text=_stdin()) == 0

    captured = capsys.readouterr()
    assert "/yoink-coordination:task" in captured.out


def test_reminder_suppressed_when_task_summary_set(capsys, monkeypatch, tmp_path):
    """Session with task_summary → stdout is quiet."""
    import state as state_mod

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    me = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="main",
        task_issue=None, started_at="2026-04-15T00:00:00Z",
        last_heartbeat="2026-04-15T00:00:00Z",
        declared_files=[], driven_by="claude-code",
        claude_session_id="s-1",
        task_summary="Implement 2FA login",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="main", worktree_path=str(tmp_path),
        session_id="s", claude_session_id="s-1",
        task_issue=None, started_at="2026-04-15T00:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
        heartbeat_cooldown_seconds=120, stale_threshold_seconds=900,
        primary_branch=None,
    )

    with patch.object(hook.github, "gh_auth_ok", return_value=True), \
         patch.object(hook.ctx_mod, "build_context", return_value=fake_ctx), \
         patch.object(hook.cfg_mod, "load_config", return_value=(fake_cfg, [])), \
         patch.object(hook.github, "list_my_status_issues",
                      return_value=[{"number": 1, "state": "OPEN", "body": body,
                                     "assignees": [{"login": "kweiza"}]}]):
        assert hook.run(stdin_text=_stdin()) == 0

    captured = capsys.readouterr()
    assert "/yoink-coordination:task" not in captured.out
