import json
import sys
from pathlib import Path
from unittest.mock import patch

# conftest.py adds lib/ to sys.path; we additionally need hooks/ for importing the hook module.
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))

import pre_tool_use as hook  # noqa


def _hook_input(tool_name="Edit", file_path="src/foo.py"):
    return json.dumps({
        "hook_event_name": "PreToolUse",
        "session_id": "test-session-id",
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
    })


def test_non_target_tool_exits_zero_noop():
    payload = _hook_input(tool_name="Read")
    assert hook.run(stdin_text=payload) == 0


def test_missing_file_path_fail_open_and_warn(capsys):
    payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Edit",
                          "session_id": "s", "tool_input": {}})
    assert hook.run(stdin_text=payload) == 0
    err = capsys.readouterr().err
    assert "[yoink]" in err


def test_gitignored_path_passthrough(tmp_path):
    payload = _hook_input(file_path=str(tmp_path / "noisy.log"))
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=True):
        assert hook.run(stdin_text=payload) == 0


def test_conflict_block_returns_nonzero(tmp_path):
    payload = _hook_input(file_path=str(tmp_path / "src" / "foo.py"))
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg(mode="block")), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", return_value=_empty_my_issue()), \
         patch.object(hook, "_find_my_session", return_value=_make_session()), \
         patch.object(hook, "_fetch_others", return_value=[
             {"path": "src/foo.py", "owners": [{"login": "alice", "branch": "a",
                                                "declared_at": "2026-04-14T10:44:17Z",
                                                "task_issue": None}]}
         ]), \
         patch.object(hook, "_write_body", return_value=True), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx()):
        rc = hook.run(stdin_text=payload)
    # Task 0 C not yet verified; spec assumes exit 2. E2E T16 will confirm.
    assert rc != 0


# Utility scaffolding
def _cfg(mode="advisory"):
    from types import SimpleNamespace
    return SimpleNamespace(
        conflict_mode=mode,
        label_prefix="yoink",
        lock_timeout_seconds=10,
        heartbeat_cooldown_seconds=120,
        stale_threshold_seconds=900,
    )


class _NullLock:
    def __enter__(self): return self
    def __exit__(self, *exc): return False


def _fake_lock_ctx():
    def factory(*a, **k): return _NullLock()
    return factory


def _empty_my_issue():
    # returns (num, parsed_state, existing_body)
    import state as state_mod
    return (1, state_mod.State(updated_at=""), "")


def _make_session():
    import state as state_mod
    return state_mod.Session(
        session_id="s", worktree_path="/tmp/wt", branch="b",
        task_issue=None, started_at="2026-04-14T10:00:00Z",
        last_heartbeat="2026-04-14T10:00:00Z",
        declared_files=[], driven_by="claude-code",
        claude_session_id="test-session-id",
    )


def _make_ctx():
    from types import SimpleNamespace
    return SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch="b", worktree_path="/tmp/wt",
        session_id="s", claude_session_id=None,
        task_issue=None, started_at="2026-04-14T10:00:00Z",
    )


def test_pretooluse_cooldown_expired_triggers_heartbeat_write(tmp_path):
    """No claim change + cooldown expired → still writes body with updated heartbeat."""
    import state as state_mod

    me = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="main",
        task_issue=None,
        started_at="2026-04-14T00:00:00Z",
        last_heartbeat="2026-04-14T00:00:00Z",  # very old (much older than cooldown)
        declared_files=[{"path": "already.py", "declared_at": "2026-04-14T00:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="test-session-id",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    payload = _hook_input(file_path=str(tmp_path / "already.py"))

    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg()), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", return_value=(1, parsed, existing_body)), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body", side_effect=lambda *a, **k: writes.append(a) or True), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value={"already.py"}), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx()):
        rc = hook.run(stdin_text=payload)
    assert rc == 0
    # Already claimed (dedup) → no structural change → but cooldown is expired,
    # so a body edit must still happen.
    assert len(writes) == 1


def test_pretooluse_cooldown_not_expired_skips_body_write(tmp_path):
    """Already-claimed dedup path + fresh heartbeat → body edit skipped."""
    import state as state_mod

    fresh_hb = "2030-01-01T12:00:00Z"  # far in the future → never stale

    me = state_mod.Session(
        session_id="s", worktree_path=str(tmp_path), branch="main",
        task_issue=None,
        started_at=fresh_hb, last_heartbeat=fresh_hb,
        declared_files=[{"path": "already.py", "declared_at": fresh_hb}],
        driven_by="claude-code",
        claude_session_id="test-session-id",
    )
    parsed = state_mod.State(updated_at="", sessions=[me])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    payload = _hook_input(file_path=str(tmp_path / "already.py"))

    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg()), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", return_value=(1, parsed, existing_body)), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body", side_effect=lambda *a, **k: writes.append(a) or True), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value={"already.py"}), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx()):
        rc = hook.run(stdin_text=payload)
    assert rc == 0
    # Neither structural change nor cooldown expired → no body edit.
    assert len(writes) == 0


def test_pretooluse_self_reconcile_when_my_session_missing(tmp_path, capsys):
    """If _find_my_session returns None, re-insert my entry and write body."""
    import state as state_mod

    # Body has no sessions (simulating SessionStart self-heal removed me).
    parsed = state_mod.State(updated_at="", sessions=[])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    payload = _hook_input(file_path=str(tmp_path / "foo.py"))

    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg()), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", return_value=(1, parsed, existing_body)), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body", side_effect=lambda *a, **k: writes.append(a) or True), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx()):
        rc = hook.run(stdin_text=payload)
    assert rc == 0
    err = capsys.readouterr().err
    assert "self-reconcile" in err.lower()
    # Reconcile must force a body write.
    assert len(writes) == 1


# ------------------------------------------------------------------
# Phase 5 telemetry: latency + refetch (M4) + conflict (M5)
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


def test_pre_tool_use_emits_latency_on_early_return(capsys):
    """Non-target tool_name → early return. Latency must still emit."""
    stdin = _hook_input(tool_name="Read")
    rc = hook.run(stdin_text=stdin)
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    latency = [l for l in lines if l["metric"] == "latency"]
    assert len(latency) == 1
    assert latency[0]["hook"] == "pre_tool_use"


def test_pre_tool_use_emits_refetch_on_self_reconcile(tmp_path, capsys):
    """When my session entry is missing from the fetched issue body, the
    self-reconcile path re-inserts it and must emit refetch reason=self_missing."""
    import state as state_mod
    parsed = state_mod.State(updated_at="", sessions=[])
    existing = ""
    payload = _hook_input(file_path=str(tmp_path / "new.py"))
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg()), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", return_value=(1, parsed, existing)), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body", return_value=True), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx()):
        rc = hook.run(stdin_text=payload)
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    refetch = [l for l in lines if l["metric"] == "refetch"]
    assert len(refetch) == 1
    assert refetch[0]["reason"] == "self_missing"


def test_pre_tool_use_emits_conflict_with_path_hash_only(tmp_path, capsys):
    """Advisory warning path: emit conflict metric with path_hash, never raw path."""
    import state as state_mod
    import telemetry
    parsed = state_mod.State(updated_at="", sessions=[_make_session()])
    existing = state_mod.render_body(parsed, login="kweiza")
    target_relpath = "src/foo.py"
    (tmp_path / "src").mkdir(exist_ok=True)
    target_fullpath = tmp_path / target_relpath
    target_fullpath.parent.mkdir(parents=True, exist_ok=True)
    payload = _hook_input(file_path=str(target_fullpath))
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg(mode="advisory")), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", return_value=(1, parsed, existing)), \
         patch.object(hook, "_fetch_others", return_value=[
             {"path": target_relpath, "owners": [{"login": "alice", "branch": "a",
                                                  "declared_at": "2026-04-14T10:44:17Z",
                                                  "task_issue": None}]}
         ]), \
         patch.object(hook, "_write_body", return_value=True), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx()):
        rc = hook.run(stdin_text=payload)
    assert rc == 0
    err = capsys.readouterr().err
    lines = _metric_lines(err)
    conflict = [l for l in lines if l["metric"] == "conflict"]
    assert len(conflict) == 1
    assert conflict[0]["path_hash"] == telemetry.path_hash(target_relpath)
    metric_lines_raw = [ln for ln in err.splitlines() if ln.startswith("[yoink-metric] ")]
    for ln in metric_lines_raw:
        assert target_relpath not in ln, f"Raw path leaked into metric line: {ln}"
