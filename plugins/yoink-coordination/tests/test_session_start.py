import json, sys
from unittest.mock import patch, MagicMock
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))

@patch("github.label_exists", return_value=True)
@patch("github.gh_auth_ok", return_value=True)
@patch("github.list_my_status_issues")
@patch("github.create_status_issue", return_value=42)
@patch("github.edit_issue_body", return_value=True)
@patch("github.add_label", return_value=True)
@patch("github.list_other_status_issues_open", return_value=[])
@patch("context.build_context")
def test_first_session_creates_issue(bc, other, addlbl, edit, create, my, auth, labels, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from context import Context
    bc.return_value = Context("alice", "o/r", "feature/7-x", str(tmp_path),
                              "o/r#7", "uuid-1", None, "2026-04-14T10:00:00Z")
    my.return_value = []
    import session_start
    rc = session_start.main()
    assert rc == 0
    create.assert_called_once_with("alice", "yoink:status")
    edit.assert_called_once()
    addlbl.assert_called_with(42, "yoink:active")

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
@patch("github.list_my_status_issues")
@patch("github.edit_issue_body", return_value=True)
@patch("github.add_label", return_value=True)
@patch("github.list_other_status_issues_open", return_value=[])
@patch("context.build_context")
def test_multiple_issues_picks_lowest_and_warns(bc, other, addlbl, edit, my, auth, labels, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from context import Context
    bc.return_value = Context("alice", "o/r", "main", str(tmp_path),
                              None, "uuid-1", None, "2026-04-14T10:00:00Z")
    my.return_value = [
        {"number": 9, "state": "OPEN",  "body": "", "assignees": [{"login": "alice"}]},
        {"number": 4, "state": "OPEN",  "body": "", "assignees": [{"login": "alice"}]},
        {"number": 7, "state": "CLOSED","body": "", "assignees": [{"login": "alice"}]},
    ]
    import session_start
    rc = session_start.main()
    assert rc == 0
    edit.assert_called_once()
    assert edit.call_args.args[0] == 4
    err = capsys.readouterr().err
    assert "#7" in err and "#9" in err


import state as state_mod


def _stale_entry():
    return state_mod.Session(
        session_id="stale-uuid",
        worktree_path="/old/wt",
        branch="old/branch",
        task_issue=None,
        started_at="2026-04-14T00:00:00Z",
        last_heartbeat="2026-04-14T00:00:00Z",  # very old
        declared_files=[],
        driven_by="claude-code",
        claude_session_id="stale-ccs",
    )


def test_session_start_removes_stale_self_entries(capsys, monkeypatch):
    """SessionStart must drop sessions whose heartbeat is older than
    cfg.stale_threshold_seconds before upserting my new entry."""
    from types import SimpleNamespace
    import session_start as hook
    import context as ctx_mod
    import config as cfg_mod

    fake_ctx = SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="feat/new", worktree_path="/new/wt",
        session_id="new-uuid", claude_session_id="new-ccs",
        task_issue=None, started_at="2026-04-15T12:00:00Z",
    )
    fake_cfg = SimpleNamespace(
        conflict_mode="advisory", label_prefix="yoink",
        lock_timeout_seconds=10,
        heartbeat_cooldown_seconds=120,
        stale_threshold_seconds=900,
    )

    existing_state = state_mod.State(updated_at="", sessions=[_stale_entry()])
    existing_body = state_mod.render_body(existing_state, login="kweiza")

    writes = []
    def fake_edit_body(num, body):
        writes.append((num, body))
        return True

    # Null-lock context manager
    class _NullLock:
        def __enter__(self): return self
        def __exit__(self, *exc): return False

    with patch.object(hook.github, "gh_auth_ok", return_value=True), \
         patch.object(hook.github, "label_exists", return_value=True), \
         patch.object(hook.ctx_mod, "build_context", return_value=fake_ctx), \
         patch.object(hook.cfg_mod, "load_config", return_value=(fake_cfg, [])), \
         patch.object(hook, "_lock_path", return_value=Path("/tmp/yoink-test.lock")), \
         patch.object(hook.lock, "acquire", return_value=_NullLock()), \
         patch.object(hook.github, "list_my_status_issues",
                      return_value=[{"number": 1, "state": "OPEN", "body": existing_body,
                                     "assignees": [{"login": "kweiza"}]}]), \
         patch.object(hook.github, "edit_issue_body", side_effect=fake_edit_body), \
         patch.object(hook.github, "add_label"), \
         patch.object(hook, "_print_other_members"):
        rc = hook.main()

    assert rc == 0
    err = capsys.readouterr().err
    assert "self-heal" in err.lower()
    assert "1 stale" in err
    # Body edit must have been called once, and the persisted body must not
    # contain the stale session_id.
    assert len(writes) == 1
    _, written_body = writes[0]
    assert "stale-uuid" not in written_body
    assert "new-uuid" in written_body


# ------------------------------------------------------------------
# Phase 5 telemetry: latency always, self_heal on removal
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


def test_session_start_emits_latency_even_on_early_return(capsys, monkeypatch):
    """gh auth missing → early return → latency line must still emit."""
    import session_start
    monkeypatch.setattr(session_start.github, "gh_auth_ok", lambda: False)
    rc = session_start.main()
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    latency = [l for l in lines if l["metric"] == "latency"]
    assert len(latency) == 1
    assert latency[0]["hook"] == "session_start"
    assert isinstance(latency[0]["duration_ms"], int)


def test_session_start_emits_self_heal_when_stale_removed(capsys, monkeypatch):
    """When self-heal removes ≥1 stale entry, emit self_heal metric with count.
    Also verify latency line is still emitted in the same run."""
    import session_start
    import state as state_mod
    import context as ctx_mod
    from types import SimpleNamespace

    # Stub gh I/O so main() progresses into self-heal.
    monkeypatch.setattr(session_start.github, "gh_auth_ok", lambda: True)
    monkeypatch.setattr(session_start.github, "label_exists", lambda n: True)

    fake_ctx = SimpleNamespace(
        login="alice",
        repo_name_with_owner="alice/repo",
        worktree_path="/tmp/x",
        branch="main",
        task_issue=None,
        started_at="2026-04-15T10:00:00Z",
        session_id="s-new",
        claude_session_id="ccs-new",
    )
    monkeypatch.setattr(ctx_mod, "build_context", lambda: fake_ctx)
    fake_cfg = SimpleNamespace(
        label_prefix="yoink",
        lock_timeout_seconds=10,
        heartbeat_cooldown_seconds=120,
        stale_threshold_seconds=900,
    )
    monkeypatch.setattr(
        session_start.cfg_mod, "load_config", lambda d: (fake_cfg, []),
    )

    # Pre-existing body with one stale session (old heartbeat).
    stale_session = state_mod.Session(
        session_id="s-old", worktree_path="/tmp/x", branch="main",
        task_issue=None,
        started_at="2026-03-01T00:00:00Z",
        last_heartbeat="2026-03-01T00:00:00Z",  # much older than 900s threshold
        declared_files=[],
        driven_by="claude-code",
        claude_session_id="ccs-old",
    )
    body = state_mod.render_body(
        state_mod.State(updated_at="2026-03-01T00:00:00Z",
                         sessions=[stale_session]),
        login="alice", preserve_tail_from="",
    )
    monkeypatch.setattr(
        session_start.github, "list_my_status_issues",
        lambda login, lbl: [{"number": 42, "state": "OPEN", "body": body,
                              "assignees": [{"login": "alice"}]}],
    )
    monkeypatch.setattr(session_start.github, "edit_issue_body",
                        lambda num, b: True)
    monkeypatch.setattr(session_start.github, "add_label",
                        lambda num, lbl: True)
    monkeypatch.setattr(session_start.github, "list_other_status_issues_open",
                        lambda login, lbl: [])

    # Pin "now" far past the stale heartbeat.
    monkeypatch.setattr(
        ctx_mod, "now_utc_iso", lambda: "2026-04-15T10:00:00Z",
    )

    # Bypass the filesystem lock using a no-op context manager.
    class _NullLock:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
    monkeypatch.setattr(session_start.lock, "acquire",
                        lambda *a, **k: _NullLock())

    rc = session_start.main()
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    self_heal = [l for l in lines if l["metric"] == "self_heal"]
    assert len(self_heal) == 1
    assert self_heal[0]["stale_removed"] == 1
    latency = [l for l in lines if l["metric"] == "latency"]
    assert len(latency) == 1
