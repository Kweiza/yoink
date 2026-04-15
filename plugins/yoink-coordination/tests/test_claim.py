import claim

NOW = "2026-04-14T10:47:29Z"


def test_acquire_adds_new_entry_when_absent():
    declared = [{"path": "a.py", "declared_at": "2026-04-14T10:00:00Z"}]
    out, changed = claim.acquire(declared, "b.py", now=NOW)
    assert changed is True
    assert {"path": "b.py", "declared_at": NOW} in out


def test_acquire_idempotent_when_already_claimed():
    declared = [{"path": "a.py", "declared_at": "2026-04-14T10:00:00Z"}]
    out, changed = claim.acquire(declared, "a.py", now=NOW)
    assert changed is False
    assert out == declared


def test_self_cleanup_removes_paths_with_no_diff():
    declared = [
        {"path": "a.py", "declared_at": NOW},
        {"path": "b.py", "declared_at": NOW},
        {"path": "c.py", "declared_at": NOW},
    ]
    dirty = {"b.py"}  # only b has working-tree diff
    out, removed = claim.self_cleanup(declared, dirty_paths=dirty)
    assert [e["path"] for e in out] == ["b.py"]
    assert set(removed) == {"a.py", "c.py"}


def test_self_cleanup_skipped_when_dirty_is_none():
    declared = [{"path": "a.py", "declared_at": NOW}]
    out, removed = claim.self_cleanup(declared, dirty_paths=None)
    # None means git status failed; keep everything.
    assert out == declared
    assert removed == []


def test_release_after_commit_removes_only_committed_paths():
    declared = [
        {"path": "a.py", "declared_at": NOW},
        {"path": "b.py", "declared_at": NOW},
    ]
    out, removed = claim.release(declared, committed_paths={"a.py", "x.py"})
    assert [e["path"] for e in out] == ["b.py"]
    assert set(removed) == {"a.py"}


def test_forward_compat_preserves_unknown_fields():
    declared = [{"path": "a.py", "declared_at": NOW, "reason": "future-phase5"}]
    out, _ = claim.acquire(declared, "b.py", now=NOW)
    assert out[0] == {"path": "a.py", "declared_at": NOW, "reason": "future-phase5"}


def _sess(session_id="s", last_heartbeat="2026-04-14T10:00:00Z",
          started_at="2026-04-14T10:00:00Z", worktree_path="/w",
          branch="main", claude_session_id=None):
    """Test helper constructing a Session dataclass for stale tests."""
    import state as state_mod
    return state_mod.Session(
        session_id=session_id,
        worktree_path=worktree_path,
        branch=branch,
        task_issue=None,
        started_at=started_at,
        last_heartbeat=last_heartbeat,
        declared_files=[],
        driven_by="claude-code",
        claude_session_id=claude_session_id,
    )

NOW_P4 = "2026-04-14T11:00:00Z"  # 1 hour after the fixture default

def test_find_stale_sessions_returns_only_stale():
    fresh = _sess(session_id="fresh", last_heartbeat="2026-04-14T10:58:00Z")  # 2m ago
    stale = _sess(session_id="stale", last_heartbeat="2026-04-14T10:00:00Z")  # 60m ago
    result = claim.find_stale_sessions([fresh, stale], NOW_P4, threshold_seconds=900)
    assert [s.session_id for s in result] == ["stale"]

def test_find_stale_sessions_all_fresh_returns_empty():
    fresh1 = _sess(session_id="a", last_heartbeat="2026-04-14T10:58:00Z")
    fresh2 = _sess(session_id="b", last_heartbeat="2026-04-14T10:59:00Z")
    assert claim.find_stale_sessions([fresh1, fresh2], NOW_P4, threshold_seconds=900) == []

def test_find_stale_sessions_empty_heartbeat_fallback_to_started_at():
    # last_heartbeat empty but started_at fresh → not stale
    s_fresh = _sess(last_heartbeat="", started_at="2026-04-14T10:58:00Z")
    assert claim.find_stale_sessions([s_fresh], NOW_P4, threshold_seconds=900) == []
    # last_heartbeat empty but started_at stale → stale
    s_stale = _sess(last_heartbeat="", started_at="2026-04-14T10:00:00Z")
    assert claim.find_stale_sessions([s_stale], NOW_P4, threshold_seconds=900) == [s_stale]

def test_find_stale_sessions_malformed_iso_excluded_as_not_stale():
    broken = _sess(last_heartbeat="not-an-iso", started_at="also-broken")
    # per-session fail-safe: excluded from stale list (conservatively not-stale)
    result = claim.find_stale_sessions([broken], NOW_P4, threshold_seconds=900)
    assert result == []

def test_find_stale_sessions_mixed_malformed_and_valid():
    broken = _sess(session_id="broken", last_heartbeat="bad")
    stale = _sess(session_id="stale", last_heartbeat="2026-04-14T10:00:00Z")
    fresh = _sess(session_id="fresh", last_heartbeat="2026-04-14T10:58:00Z")
    result = claim.find_stale_sessions([broken, stale, fresh], NOW_P4, threshold_seconds=900)
    # broken is excluded (safe), stale returned, fresh not.
    assert [s.session_id for s in result] == ["stale"]

def test_remove_sessions_filters_by_identity():
    a = _sess(session_id="a")
    b = _sess(session_id="b")
    c = _sess(session_id="c")
    result = claim.remove_sessions([a, b, c], to_remove=[b])
    assert [s.session_id for s in result] == ["a", "c"]

def test_remove_sessions_empty_to_remove_is_noop():
    a = _sess(session_id="a")
    b = _sess(session_id="b")
    result = claim.remove_sessions([a, b], to_remove=[])
    assert result == [a, b]
