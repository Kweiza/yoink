"""Tests for hooks/stop.py.

v0.3.26: Stop hook's only side effect is heartbeat-on-cooldown-expiry.
Release detection moved to the GitHub Actions workflow, so the prior
`release_merged` / `path_ahead_of_primary` tests are gone.
"""
import json as _json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# conftest handles lib/; add hooks/
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))

import stop  # noqa


def _cfg(**overrides):
    defaults = dict(
        conflict_mode="advisory",
        label_prefix="yoink",
        lock_timeout_seconds=10,
        heartbeat_cooldown_seconds=120,
        stale_threshold_seconds=900,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_importable_and_run_returns_zero_without_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert stop.run(stdin_text="") == 0


def test_non_json_stdin_fail_open(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert stop.run(stdin_text="not-json") == 0


def test_session_id_captured_from_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    payload = _json.dumps({"hook_event_name": "Stop", "session_id": "s-123"})
    with patch.object(stop.github, "gh_auth_ok", return_value=False):
        assert stop.run(stdin_text=payload) == 0


def test_stop_cooldown_expired_triggers_heartbeat_write(monkeypatch, tmp_path):
    """No structural change needed — expired cooldown alone triggers a body
    edit so the human-facing heartbeat column stays fresh."""
    import state as state_mod

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    me = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="main",
        task_issue=None,
        started_at="2026-04-14T00:00:00Z",
        last_heartbeat="2026-04-14T00:00:00Z",  # very old
        declared_files=[{"path": "a.py", "declared_at": "2026-04-14T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="s-123",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="main", worktree_path=str(tmp_path),
        session_id="s", claude_session_id="s-123",
        task_issue=None, started_at="2026-04-14T00:00:00Z",
    )

    writes = []
    with patch.object(stop.github, "gh_auth_ok", return_value=True), \
         patch.object(stop.ctx_mod, "build_context", return_value=fake_ctx), \
         patch.object(stop.cfg_mod, "load_config", return_value=(_cfg(), [])), \
         patch.object(stop.github, "list_my_status_issues",
                       return_value=[{"number": 1, "state": "OPEN",
                                      "body": existing_body,
                                      "assignees": [{"login": "kweiza"}]}]), \
         patch.object(stop.github, "edit_issue_body",
                       side_effect=lambda n, b: writes.append(b) or True), \
         patch.object(stop.lock, "acquire") as lock_mock:
        lock_mock.return_value.__enter__ = lambda self: None
        lock_mock.return_value.__exit__ = lambda self, *a: False

        payload = _json.dumps({"hook_event_name": "Stop", "session_id": "s-123"})
        assert stop.run(stdin_text=payload) == 0

    assert len(writes) == 1


def test_stop_skips_write_when_cooldown_fresh(monkeypatch, tmp_path):
    """Fresh heartbeat → no body edit."""
    import state as state_mod

    fresh = "2030-01-01T12:00:00Z"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    me = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="main",
        task_issue=None,
        started_at=fresh, last_heartbeat=fresh,
        declared_files=[{"path": "a.py", "declared_at": fresh}],
        driven_by="claude-code",
        claude_session_id="s-fresh",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="main", worktree_path=str(tmp_path),
        session_id="s", claude_session_id="s-fresh",
        task_issue=None, started_at=fresh,
    )

    writes = []
    with patch.object(stop.github, "gh_auth_ok", return_value=True), \
         patch.object(stop.ctx_mod, "build_context", return_value=fake_ctx), \
         patch.object(stop.cfg_mod, "load_config", return_value=(_cfg(), [])), \
         patch.object(stop.github, "list_my_status_issues",
                       return_value=[{"number": 1, "state": "OPEN",
                                      "body": existing_body,
                                      "assignees": [{"login": "kweiza"}]}]), \
         patch.object(stop.github, "edit_issue_body",
                       side_effect=lambda n, b: writes.append(b) or True), \
         patch.object(stop.lock, "acquire") as lock_mock:
        lock_mock.return_value.__enter__ = lambda self: None
        lock_mock.return_value.__exit__ = lambda self, *a: False

        payload = _json.dumps({"hook_event_name": "Stop", "session_id": "s-fresh"})
        assert stop.run(stdin_text=payload) == 0

    assert writes == []


def test_stop_noop_when_no_matching_session(monkeypatch, tmp_path):
    """My issue has a session for a different ccs → Stop leaves it alone."""
    import state as state_mod

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    other = state_mod.Session(
        session_id="other", worktree_path=str(tmp_path), branch="main",
        task_issue=None,
        started_at="2026-04-14T00:00:00Z",
        last_heartbeat="2026-04-14T00:00:00Z",
        declared_files=[],
        driven_by="claude-code",
        claude_session_id="ccs-other",
    )
    parsed = state_mod.State(updated_at="", sessions=[other])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="main", worktree_path=str(tmp_path),
        session_id="s", claude_session_id="ccs-me",
        task_issue=None, started_at="2026-04-15T00:00:00Z",
    )

    writes = []
    with patch.object(stop.github, "gh_auth_ok", return_value=True), \
         patch.object(stop.ctx_mod, "build_context", return_value=fake_ctx), \
         patch.object(stop.cfg_mod, "load_config", return_value=(_cfg(), [])), \
         patch.object(stop.github, "list_my_status_issues",
                       return_value=[{"number": 1, "state": "OPEN",
                                      "body": existing_body,
                                      "assignees": [{"login": "kweiza"}]}]), \
         patch.object(stop.github, "edit_issue_body",
                       side_effect=lambda n, b: writes.append(b) or True), \
         patch.object(stop.lock, "acquire") as lock_mock:
        lock_mock.return_value.__enter__ = lambda self: None
        lock_mock.return_value.__exit__ = lambda self, *a: False

        payload = _json.dumps({"hook_event_name": "Stop", "session_id": "ccs-me"})
        assert stop.run(stdin_text=payload) == 0

    assert writes == []


def test_stop_emits_latency_on_run(capsys, monkeypatch):
    monkeypatch.setattr(stop.github, "gh_auth_ok", lambda: False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/tmp")
    assert stop.run(stdin_text="{}") == 0
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if ln.startswith("[yoink-metric] ")]
    parsed = [_json.loads(ln.split(" ", 1)[1]) for ln in lines]
    latency = [p for p in parsed if p["metric"] == "latency"]
    assert len(latency) == 1
    assert latency[0]["hook"] == "stop"
