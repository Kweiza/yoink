import sys
from pathlib import Path
from unittest.mock import patch

# conftest handles lib/; add hooks/
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))

import stop  # noqa


def test_importable_and_run_returns_zero_without_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert stop.run(stdin_text="") == 0


def test_non_json_stdin_fail_open(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert stop.run(stdin_text="not-json") == 0


def test_session_id_captured_from_payload(monkeypatch, tmp_path):
    import json as _json
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    payload = _json.dumps({"hook_event_name": "Stop", "session_id": "s-123"})
    with patch.object(stop.github, "gh_auth_ok", return_value=False):
        # Early exit via gh_auth_ok=False, but the session_id should have been
        # read from the payload before that point.
        assert stop.run(stdin_text=payload) == 0


def test_stop_cooldown_expired_triggers_heartbeat_write(monkeypatch, tmp_path):
    """Stop hook: even without self-cleanup changes, cooldown expiry triggers body edit."""
    import json as _json
    import state as state_mod
    from types import SimpleNamespace

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
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
        heartbeat_cooldown_seconds=120, stale_threshold_seconds=900,
        primary_branch=None,
    )

    writes = []
    from unittest.mock import patch as _patch
    with _patch.object(stop.github, "gh_auth_ok", return_value=True), \
         _patch.object(stop.ctx_mod, "build_context", return_value=fake_ctx), \
         _patch.object(stop.cfg_mod, "load_config", return_value=(fake_cfg, [])), \
         _patch.object(stop.github, "list_my_status_issues",
                       return_value=[{"number": 1, "state": "OPEN", "body": existing_body,
                                      "assignees": [{"login": "kweiza"}]}]), \
         _patch.object(stop.github, "edit_issue_body",
                       side_effect=lambda n, b: writes.append(b) or True), \
         _patch.object(stop.gitops, "working_tree_paths", return_value={"a.py"}), \
         _patch.object(stop.lock, "acquire") as lock_mock:
        lock_mock.return_value.__enter__ = lambda self: None
        lock_mock.return_value.__exit__ = lambda self, *a: False

        payload = _json.dumps({"hook_event_name": "Stop", "session_id": "s-123"})
        assert stop.run(stdin_text=payload) == 0

    # Working tree kept a.py → self-cleanup no change. But cooldown is very
    # expired, so a heartbeat-only write must still occur.
    assert len(writes) == 1

import re
import json as _json

def test_stop_emits_latency_on_run(capsys, monkeypatch):
    import stop
    monkeypatch.setattr(stop.github, "gh_auth_ok", lambda: False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/tmp")
    rc = stop.run(stdin_text="{}")
    assert rc == 0
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if ln.startswith("[yoink-metric] ")]
    parsed = [_json.loads(ln.split(" ", 1)[1]) for ln in lines]
    latency = [p for p in parsed if p["metric"] == "latency"]
    assert len(latency) == 1
    assert latency[0]["hook"] == "stop"


def test_stop_releases_merged_path_and_emits_release_metric(monkeypatch, tmp_path, capsys):
    """Declared path not in working tree AND not ahead of primary → release,
    `release` metric emitted with held_seconds + trigger=merged."""
    import json as _json
    import state as state_mod
    from types import SimpleNamespace

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    declared_at = "2026-04-15T00:00:00Z"
    me = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="feat",
        task_issue=None,
        started_at="2026-04-15T00:00:00Z",
        last_heartbeat="2026-04-15T06:59:00Z",
        declared_files=[{"path": "merged.py", "declared_at": declared_at}],
        driven_by="claude-code",
        claude_session_id="s-abc",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="feat", worktree_path=str(tmp_path),
        session_id="s", claude_session_id="s-abc",
        task_issue=None, started_at="2026-04-15T00:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
        heartbeat_cooldown_seconds=120, stale_threshold_seconds=900,
        primary_branch="main",
    )

    writes = []
    from unittest.mock import patch as _patch
    with _patch.object(stop.github, "gh_auth_ok", return_value=True), \
         _patch.object(stop.ctx_mod, "build_context", return_value=fake_ctx), \
         _patch.object(stop.cfg_mod, "load_config", return_value=(fake_cfg, [])), \
         _patch.object(stop.github, "list_my_status_issues",
                       return_value=[{"number": 1, "state": "OPEN", "body": existing_body,
                                      "assignees": [{"login": "kweiza"}]}]), \
         _patch.object(stop.github, "edit_issue_body",
                       side_effect=lambda n, b: writes.append(b) or True), \
         _patch.object(stop.gitops, "working_tree_paths", return_value=set()), \
         _patch.object(stop.gitops, "path_ahead_of_primary", return_value=False), \
         _patch.object(stop.lock, "acquire") as lock_mock:
        lock_mock.return_value.__enter__ = lambda self: None
        lock_mock.return_value.__exit__ = lambda self, *a: False

        payload = _json.dumps({"hook_event_name": "Stop", "session_id": "s-abc"})
        assert stop.run(stdin_text=payload) == 0

    assert len(writes) == 1
    body = writes[0]
    # v0.3.15: when declared_files becomes empty after release, the
    # entire entry is dropped (task complete).
    assert '"sessions": []' in body
    err = capsys.readouterr().err
    lines = [_json.loads(ln.split(" ", 1)[1]) for ln in err.splitlines()
             if ln.startswith("[yoink-metric] ")]
    releases = [l for l in lines if l["metric"] == "release"]
    assert len(releases) == 1
    assert releases[0]["hook"] == "stop"
    assert releases[0]["trigger"] in ("merged", "reverted")
    assert isinstance(releases[0]["held_seconds"], int)
    assert releases[0]["held_seconds"] >= 0


def test_stop_keeps_path_when_ahead_of_primary(monkeypatch, tmp_path, capsys):
    """Path is not dirty but still has ahead-of-primary commits → held."""
    import json as _json
    import state as state_mod
    from types import SimpleNamespace

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    me = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="feat",
        task_issue=None, started_at="2026-04-15T00:00:00Z",
        last_heartbeat="2026-04-15T06:59:00Z",
        declared_files=[{"path": "pending.py", "declared_at": "2026-04-15T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="s-xyz",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="feat", worktree_path=str(tmp_path),
        session_id="s", claude_session_id="s-xyz",
        task_issue=None, started_at="2026-04-15T00:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
        heartbeat_cooldown_seconds=120, stale_threshold_seconds=900,
        primary_branch="main",
    )

    writes = []
    from unittest.mock import patch as _patch
    with _patch.object(stop.github, "gh_auth_ok", return_value=True), \
         _patch.object(stop.ctx_mod, "build_context", return_value=fake_ctx), \
         _patch.object(stop.cfg_mod, "load_config", return_value=(fake_cfg, [])), \
         _patch.object(stop.github, "list_my_status_issues",
                       return_value=[{"number": 1, "state": "OPEN", "body": existing_body,
                                      "assignees": [{"login": "kweiza"}]}]), \
         _patch.object(stop.github, "edit_issue_body",
                       side_effect=lambda n, b: writes.append(b) or True), \
         _patch.object(stop.gitops, "working_tree_paths", return_value=set()), \
         _patch.object(stop.gitops, "path_ahead_of_primary", return_value=True), \
         _patch.object(stop.lock, "acquire") as lock_mock:
        lock_mock.return_value.__enter__ = lambda self: None
        lock_mock.return_value.__exit__ = lambda self, *a: False

        payload = _json.dumps({"hook_event_name": "Stop", "session_id": "s-xyz"})
        assert stop.run(stdin_text=payload) == 0

    # Path kept (ahead of primary). No release metric.
    err = capsys.readouterr().err
    releases = [ln for ln in err.splitlines()
                if ln.startswith("[yoink-metric] ") and '"metric":"release"' in ln
                and '"metric":"release_applied"' not in ln]
    assert releases == []
