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
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx(str(tmp_path), "main")):
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
    # returns (num, parsed_state, existing_body, was_closed)
    import state as state_mod
    return (1, state_mod.State(updated_at=""), "", False)


def _make_session():
    import state as state_mod
    return state_mod.Session(
        session_id="s", worktree_path="/tmp/wt", branch="b",
        task_issue=None, started_at="2026-04-14T10:00:00Z",
        last_heartbeat="2026-04-14T10:00:00Z",
        declared_files=[], driven_by="claude-code",
        claude_session_id="test-session-id",
    )


def _make_ctx(worktree_path: str = "/tmp/wt", branch: str = "b"):
    """v0.3.15: tests use (worktree_path, branch) for matching."""
    from types import SimpleNamespace
    return SimpleNamespace(
        login="kweiza", repo_name_with_owner="kweiza/yoink",
        branch=branch, worktree_path=worktree_path,
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
         patch.object(hook, "_fetch_my_issue", return_value=(1, parsed, existing_body, False)), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body", side_effect=lambda *a, **k: writes.append(a) or True), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value={"already.py"}), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx(str(tmp_path), "main")):
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
         patch.object(hook, "_fetch_my_issue", return_value=(1, parsed, existing_body, False)), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body", side_effect=lambda *a, **k: writes.append(a) or True), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value={"already.py"}), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx(str(tmp_path), "main")):
        rc = hook.run(stdin_text=payload)
    assert rc == 0
    # Neither structural change nor cooldown expired → no body edit.
    assert len(writes) == 0


def test_pretooluse_lazy_creates_entry_when_no_session_for_wb(tmp_path, capsys):
    """v0.3.15: if no entry exists for (worktree, branch), PreToolUse
    creates a fresh entry on first declare and writes the body. The
    'self-reconcile' stderr message was removed because under the new
    rule this is the normal lazy-create path, not exceptional recovery."""
    import state as state_mod

    parsed = state_mod.State(updated_at="", sessions=[])
    existing_body = state_mod.render_body(parsed, login="kweiza")

    payload = _hook_input(file_path=str(tmp_path / "foo.py"))

    writes = []
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg()), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", return_value=(1, parsed, existing_body, False)), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body", side_effect=lambda *a, **k: writes.append(a) or True), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx(str(tmp_path), "main")):
        rc = hook.run(stdin_text=payload)
    assert rc == 0
    # Entry created → write body.
    assert len(writes) == 1
    # _write_body signature: (issue_num, login, parsed_state, existing)
    parsed_after = writes[0][2]
    assert len(parsed_after.sessions) == 1
    assert parsed_after.sessions[0].declared_files


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


def test_pre_tool_use_lazy_create_does_not_emit_refetch(tmp_path, capsys):
    """v0.3.15: lazy entry creation is the normal path, not exceptional
    self-reconcile, so the refetch metric is no longer emitted. The
    acquire metric is still emitted for the new declared path."""
    import state as state_mod
    parsed = state_mod.State(updated_at="", sessions=[])
    existing = ""
    payload = _hook_input(file_path=str(tmp_path / "new.py"))
    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg()), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", return_value=(1, parsed, existing, False)), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body", return_value=True), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx(str(tmp_path), "main")):
        rc = hook.run(stdin_text=payload)
    assert rc == 0
    lines = _metric_lines(capsys.readouterr().err)
    assert [l for l in lines if l["metric"] == "refetch"] == []
    assert [l for l in lines if l["metric"] == "acquire"]


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
         patch.object(hook, "_fetch_my_issue", return_value=(1, parsed, existing, False)), \
         patch.object(hook, "_fetch_others", return_value=[
             {"path": target_relpath, "owners": [{"login": "alice", "branch": "a",
                                                  "declared_at": "2026-04-14T10:44:17Z",
                                                  "task_issue": None}]}
         ]), \
         patch.object(hook, "_write_body", return_value=True), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx(str(tmp_path), "main")):
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


# ---------------------------------------------------------------
# v0.3.10 lazy-create: PreToolUse creates the issue when absent
# ---------------------------------------------------------------
def test_pretooluse_lazy_creates_issue_on_first_declare(tmp_path):
    """Issue absent + file edit → create issue, insert session, write body,
    add yoink:active label."""
    payload = _hook_input(file_path=str(tmp_path / "src" / "foo.py"))

    writes = []
    created_nums = []
    labels = []

    def fake_fetch(login, label):
        return (None, None, "", False)  # triggers create path

    def fake_create(login, label):
        return 77

    def fake_write(num, login, parsed, existing):
        writes.append((num, login, parsed))
        return True

    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg()), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", side_effect=fake_fetch), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body", side_effect=fake_write), \
         patch("pre_tool_use.github.create_status_issue",
               side_effect=fake_create), \
         patch("pre_tool_use.github.add_label",
               side_effect=lambda n, lbl: labels.append((n, lbl))), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx(str(tmp_path), "main")):
        rc = hook.run(stdin_text=payload)

    assert rc == 0
    assert len(writes) == 1
    num, _login, parsed = writes[0]
    assert num == 77
    # Session entry was synthesized and declared the file
    assert len(parsed.sessions) == 1
    assert parsed.sessions[0].declared_files
    # yoink:active label attached once
    assert labels == [(77, "yoink:active")]


def test_pretooluse_lazy_create_fails_returns_zero(tmp_path, capsys):
    """Create-issue failure → fail-open (exit 0, no body write, no label)."""
    payload = _hook_input(file_path=str(tmp_path / "src" / "foo.py"))

    writes = []
    labels = []

    def fake_fetch(login, label):
        return (None, None, "", False)

    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg()), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", side_effect=fake_fetch), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body",
                      side_effect=lambda *a, **k: writes.append(a) or True), \
         patch("pre_tool_use.github.create_status_issue",
               return_value=None), \
         patch("pre_tool_use.github.add_label",
               side_effect=lambda n, lbl: labels.append((n, lbl))), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("pre_tool_use.ctx_mod.build_context", return_value=_make_ctx(str(tmp_path), "main")):
        rc = hook.run(stdin_text=payload)

    assert rc == 0
    assert writes == []
    assert labels == []


# ---------------------------------------------------------------
# v0.3.15 — _find_my_session matches by (worktree, branch) only.
# A session entry survives across Claude sessions until all its
# declared_files land on primary (release happens in stop.py only).
# ---------------------------------------------------------------
def test_find_my_session_does_not_inherit_other_session_entry():
    """v0.3.18: per-session entries. A new Claude session must NOT
    inherit another session's entry, even on the same (worktree, branch).
    The new session creates its own entry; the old session's entry
    persists separately until its own files merge to primary."""
    import state as state_mod
    prior = state_mod.Session(
        session_id="old", worktree_path="/wt", branch="main",
        task_issue=None,
        started_at="2026-04-15T10:00:00Z",
        last_heartbeat="2026-04-15T10:00:00Z",
        declared_files=[{"path": "a.py", "declared_at": "2026-04-15T10:00:00Z"}],
        driven_by="claude-code",
        claude_session_id="ccs-OLD",
        task_summary="ongoing work",
    )
    parsed = state_mod.State(updated_at="", sessions=[prior])
    from types import SimpleNamespace
    ctx = SimpleNamespace(worktree_path="/wt", branch="main",
                          claude_session_id="ccs-NEW")
    out = hook._find_my_session(parsed, hook_session_id="ccs-NEW", ctx=ctx)
    assert out is None


def test_find_my_session_falls_back_to_legacy_no_ccs_entry():
    """Legacy entry (no claude_session_id) on the same (worktree, branch)
    is matched as a one-off compatibility path."""
    import state as state_mod
    legacy = state_mod.Session(
        session_id="legacy", worktree_path="/wt", branch="main",
        task_issue=None,
        started_at="2026-04-15T10:00:00Z",
        last_heartbeat="2026-04-15T10:00:00Z",
        declared_files=[],
        driven_by="claude-code",
        claude_session_id=None,
    )
    parsed = state_mod.State(updated_at="", sessions=[legacy])
    from types import SimpleNamespace
    ctx = SimpleNamespace(worktree_path="/wt", branch="main",
                          claude_session_id="ccs-NEW")
    assert hook._find_my_session(parsed, hook_session_id="ccs-NEW", ctx=ctx) is legacy


def test_find_my_session_returns_none_when_no_entry_for_wb():
    import state as state_mod
    parsed = state_mod.State(updated_at="", sessions=[])
    from types import SimpleNamespace
    ctx = SimpleNamespace(worktree_path="/wt", branch="main",
                          claude_session_id="ccs-NEW")
    assert hook._find_my_session(parsed, hook_session_id="ccs-NEW", ctx=ctx) is None


def test_pretooluse_reopens_closed_issue_on_first_declare(tmp_path):
    """v0.3.27: if list_my_status_issues returns an issue in CLOSED state
    (typical after Actions release workflow closed it), PreToolUse must
    reopen it and re-attach the active label before writing a fresh
    session entry. Otherwise the issue stays closed / invisible."""
    import state as state_mod
    parsed = state_mod.State(updated_at="", sessions=[])
    existing = ""

    payload = _hook_input(file_path=str(tmp_path / "new.py"))

    reopen_calls = []
    label_calls = []

    def fake_fetch(login, label):
        return (77, parsed, existing, True)  # was_closed=True

    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg()), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", side_effect=fake_fetch), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body", return_value=True), \
         patch("pre_tool_use.github.reopen_issue",
               side_effect=lambda n: reopen_calls.append(n) or True), \
         patch("pre_tool_use.github.add_label",
               side_effect=lambda n, lbl: label_calls.append((n, lbl))), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("pre_tool_use.ctx_mod.build_context",
               return_value=_make_ctx(str(tmp_path), "main")):
        rc = hook.run(stdin_text=payload)

    assert rc == 0
    assert reopen_calls == [77]
    assert label_calls == [(77, "yoink:active")]


def test_pretooluse_does_not_reopen_open_issue(tmp_path):
    import state as state_mod
    parsed = state_mod.State(updated_at="", sessions=[])
    existing = ""

    payload = _hook_input(file_path=str(tmp_path / "new.py"))
    reopen_calls = []

    def fake_fetch(login, label):
        return (77, parsed, existing, False)  # was_closed=False

    with patch.object(hook, "_project_dir", return_value=tmp_path), \
         patch.object(hook, "_is_gitignored", return_value=False), \
         patch.object(hook, "_gh_auth_ok", return_value=True), \
         patch.object(hook, "_load_config", return_value=_cfg()), \
         patch.object(hook, "_acquire_lock_ctx", _fake_lock_ctx()), \
         patch.object(hook, "_fetch_my_issue", side_effect=fake_fetch), \
         patch.object(hook, "_fetch_others", return_value=[]), \
         patch.object(hook, "_write_body", return_value=True), \
         patch("pre_tool_use.github.reopen_issue",
               side_effect=lambda n: reopen_calls.append(n) or True), \
         patch("pre_tool_use.github.add_label"), \
         patch("pre_tool_use.gitops.working_tree_paths", return_value=set()), \
         patch("pre_tool_use.ctx_mod.build_context",
               return_value=_make_ctx(str(tmp_path), "main")):
        rc = hook.run(stdin_text=payload)
    assert rc == 0
    assert reopen_calls == []
