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


# ---------------------------------------------------------------
# v0.3.11 cache fast path
# ---------------------------------------------------------------
def test_upsubmit_cache_hit_skips_gh_and_no_reminder(tmp_path, monkeypatch, capsys):
    """When the task_cache stamp exists for this worktree+branch, the hook
    must early-return without calling gh and without printing a reminder."""
    import importlib, sys as _sys
    from pathlib import Path as _Path
    hooks = _Path(__file__).resolve().parents[1] / "hooks"
    if str(hooks) not in _sys.path:
        _sys.path.insert(0, str(hooks))
    import user_prompt_submit as hook

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("YOINK_TASK_CACHE_ROOT", str(tmp_path / "cache"))
    importlib.reload(hook.task_cache)

    from types import SimpleNamespace
    fake_ctx = SimpleNamespace(
        login="alice", repo_name_with_owner="o/r",
        worktree_path="/wt", branch="feat/x",
        session_id="s", claude_session_id="ccs",
        task_issue=None, started_at="2026-04-15T10:00:00Z",
    )
    monkeypatch.setattr(hook.ctx_mod, "build_context", lambda: fake_ctx)

    # Pre-create the stamp file to simulate a prior CLI run for this ccs.
    hook.task_cache.mark_set("/wt", "feat/x", "s")

    gh_called = {"n": 0}
    def spy(*a, **k):
        gh_called["n"] += 1
        return []
    monkeypatch.setattr(hook.github, "list_my_status_issues", spy)
    monkeypatch.setattr(hook.github, "gh_auth_ok", lambda: True)

    payload = '{"session_id":"s"}'
    rc = hook.run(stdin_text=payload)
    out = capsys.readouterr()
    assert rc == 0
    assert gh_called["n"] == 0, "gh round-trip must be skipped on cache hit"
    assert "SYSTEM INSTRUCTION" not in out.out
    assert "SYSTEM INSTRUCTION" not in out.err


def test_upsubmit_stronger_reminder_language():
    """The reminder text must be imperative ('MUST', 'BEFORE', first
    action framing) so Claude interprets it as instruction, not trivia."""
    import importlib, sys as _sys
    from pathlib import Path as _Path
    hooks = _Path(__file__).resolve().parents[1] / "hooks"
    if str(hooks) not in _sys.path:
        _sys.path.insert(0, str(hooks))
    import user_prompt_submit as hook
    importlib.reload(hook)
    r = hook._REMINDER
    assert "SYSTEM INSTRUCTION" in r
    assert "BEFORE" in r
    assert "/yoink-coordination:task" in r
    # v0.3.17: reminder must explicitly tell Claude to enumerate files.
    assert "--files" in r


def test_upsubmit_does_not_inherit_other_session_task(tmp_path, monkeypatch):
    """v0.3.13: when our session_id differs from the only entry in the
    issue, treat it as 'no matching session' (silent), not as inherited
    'task is set'. Crucially the new session's PreToolUse will create a
    fresh entry — at which point this hook will start prompting again."""
    import importlib, sys as _sys
    from pathlib import Path as _Path
    hooks = _Path(__file__).resolve().parents[1] / "hooks"
    if str(hooks) not in _sys.path:
        _sys.path.insert(0, str(hooks))
    monkeypatch.setenv("YOINK_TASK_CACHE_ROOT", str(tmp_path / "cache"))
    import task_cache as tc
    importlib.reload(tc)
    import user_prompt_submit as hook
    importlib.reload(hook)
    import state as state_mod

    other = state_mod.Session(
        session_id="old", worktree_path="/wt", branch="main",
        task_issue=None,
        started_at="2026-04-15T10:00:00Z",
        declared_files=[],
        driven_by="claude-code",
        claude_session_id="ccs-OLD",
        task_summary="old session summary",
    )
    body = state_mod.render_body(
        state_mod.State(updated_at="2026-04-15T10:00:00Z", sessions=[other]),
        login="alice", preserve_tail_from="",
    )
    from types import SimpleNamespace
    ctx = SimpleNamespace(worktree_path="/wt", branch="main",
                          claude_session_id="ccs-NEW", login="alice")
    cfg = SimpleNamespace(label_prefix="yoink")
    monkeypatch.setattr(
        hook.github, "list_my_status_issues",
        lambda l, lab: [{"number": 1, "body": body,
                          "assignees": [{"login": "alice"}]}],
    )
    # v0.3.18: per-session matching by ccs. ccs-NEW has no entry → NO_ENTRY
    # (won't inherit ccs-OLD's task). ccs-OLD's own entry is set.
    assert hook._evaluate_task_state(ctx, cfg, "ccs-NEW") == hook._STATE_NO_ENTRY
    assert hook._evaluate_task_state(ctx, cfg, "ccs-OLD") == hook._STATE_SET


def test_upsubmit_no_entry_does_not_mark_cache(tmp_path, monkeypatch):
    """v0.3.14 critical fix: when there is no matching session entry yet
    (new session, hasn't declared a file), the hook must NOT stamp the
    cache. Otherwise the next prompt would fast-path silent forever even
    though a PreToolUse later this turn could create an entry with an
    empty task_summary that needs nagging."""
    import importlib, sys as _sys
    from pathlib import Path as _Path
    hooks = _Path(__file__).resolve().parents[1] / "hooks"
    if str(hooks) not in _sys.path:
        _sys.path.insert(0, str(hooks))
    monkeypatch.setenv("YOINK_TASK_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    import task_cache as tc
    importlib.reload(tc)
    import user_prompt_submit as hook
    importlib.reload(hook)

    from types import SimpleNamespace
    fake_ctx = SimpleNamespace(
        login="alice", repo_name_with_owner="o/r",
        worktree_path="/wt", branch="main",
        session_id="s", claude_session_id="ccs-fresh",
        task_issue=None, started_at="2026-04-15T10:00:00Z",
    )
    monkeypatch.setattr(hook.ctx_mod, "build_context", lambda: fake_ctx)
    monkeypatch.setattr(hook.github, "gh_auth_ok", lambda: True)
    monkeypatch.setattr(hook.github, "list_my_status_issues", lambda l, lab: [])
    monkeypatch.setattr(
        hook.cfg_mod, "load_config",
        lambda d: (SimpleNamespace(label_prefix="yoink"), []),
    )
    rc = hook.run(stdin_text='{"session_id":"ccs-fresh"}')
    assert rc == 0
    # The critical assertion: no stamp written when no entry exists.
    assert hook.task_cache.is_set("/wt", "main") is False


def test_upsubmit_empty_summary_prints_reminder_and_no_cache(tmp_path, monkeypatch, capsys):
    """When entry exists with empty task_summary, print reminder AND do
    not mark cache (cache is only for confirmed-set state)."""
    import importlib, sys as _sys
    from pathlib import Path as _Path
    hooks = _Path(__file__).resolve().parents[1] / "hooks"
    if str(hooks) not in _sys.path:
        _sys.path.insert(0, str(hooks))
    monkeypatch.setenv("YOINK_TASK_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    import task_cache as tc
    importlib.reload(tc)
    import user_prompt_submit as hook
    importlib.reload(hook)
    import state as state_mod

    me = state_mod.Session(
        session_id="s", worktree_path="/wt", branch="main",
        task_issue=None,
        started_at="2026-04-15T10:00:00Z",
        declared_files=[{"path": "x.py", "declared_at": "2026-04-15T10:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="ccs-me",
        task_summary=None,
    )
    body = state_mod.render_body(
        state_mod.State(updated_at="2026-04-15T10:00:00Z", sessions=[me]),
        login="alice", preserve_tail_from="",
    )
    from types import SimpleNamespace
    monkeypatch.setattr(hook.ctx_mod, "build_context",
                        lambda: SimpleNamespace(
                            login="alice", repo_name_with_owner="o/r",
                            worktree_path="/wt", branch="main",
                            session_id="s", claude_session_id="ccs-me",
                            task_issue=None,
                            started_at="2026-04-15T10:00:00Z"))
    monkeypatch.setattr(hook.github, "gh_auth_ok", lambda: True)
    monkeypatch.setattr(
        hook.github, "list_my_status_issues",
        lambda l, lab: [{"number": 1, "body": body, "assignees": [{"login": "alice"}]}],
    )
    monkeypatch.setattr(
        hook.cfg_mod, "load_config",
        lambda d: (SimpleNamespace(label_prefix="yoink"), []),
    )
    rc = hook.run(stdin_text='{"session_id":"ccs-me"}')
    assert rc == 0
    out = capsys.readouterr()
    assert "SYSTEM INSTRUCTION" in out.out  # reminder printed
    assert hook.task_cache.is_set("/wt", "main") is False  # NOT cached


def test_upsubmit_no_entry_now_prints_reminder(tmp_path, monkeypatch, capsys):
    """v0.3.16: no entry yet → still print reminder so Claude is told to
    record the task BEFORE doing the file edit (otherwise the reminder
    fires one prompt late)."""
    import importlib, sys as _sys
    from pathlib import Path as _Path
    hooks = _Path(__file__).resolve().parents[1] / "hooks"
    if str(hooks) not in _sys.path:
        _sys.path.insert(0, str(hooks))
    monkeypatch.setenv("YOINK_TASK_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    import task_cache as tc
    importlib.reload(tc)
    import user_prompt_submit as hook
    importlib.reload(hook)

    from types import SimpleNamespace
    fake_ctx = SimpleNamespace(
        login="alice", repo_name_with_owner="o/r",
        worktree_path="/wt", branch="main",
        session_id="s", claude_session_id="ccs",
        task_issue=None, started_at="2026-04-15T10:00:00Z",
    )
    monkeypatch.setattr(hook.ctx_mod, "build_context", lambda: fake_ctx)
    monkeypatch.setattr(hook.github, "gh_auth_ok", lambda: True)
    monkeypatch.setattr(hook.github, "list_my_status_issues", lambda l, lab: [])
    monkeypatch.setattr(
        hook.cfg_mod, "load_config",
        lambda d: (SimpleNamespace(label_prefix="yoink"), []),
    )
    rc = hook.run(stdin_text='{"session_id":"ccs"}')
    assert rc == 0
    out = capsys.readouterr()
    assert "SYSTEM INSTRUCTION" in out.out
    # Still no cache stamp on no-entry — next prompt re-checks.
    assert tc.is_set("/wt", "main") is False
