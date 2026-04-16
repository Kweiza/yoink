"""Tests for lib/render.py (v0.3.28 — heartbeat + stale markers gone)."""
import render
import state as state_mod
from state import State, Session


def _s(ws="/p/a", b="main", ti=None):
    return Session("sid", ws, b, ti,
                   started_at="2026-04-14T10:00:00Z",
                   declared_files=[], driven_by="claude-code",
                   claude_session_id=None)


def _sess(branch="main", task_issue=None, claude_session_id=None,
          worktree_path="/w"):
    return state_mod.Session(
        session_id="s-" + branch,
        worktree_path=worktree_path,
        branch=branch,
        task_issue=task_issue,
        started_at="2026-04-14T10:00:00Z",
        declared_files=[],
        driven_by="claude-code",
        claude_session_id=claude_session_id,
    )


def _member(login, sessions):
    return {"login": login, "issue_number": 1,
            "state": state_mod.State(updated_at="", sessions=sessions)}


def test_render_markdown_empty():
    out = render.team_status_markdown([])
    assert "No team members active" in out or "no team members active" in out.lower()


def test_render_markdown_one_user():
    members = [{"login": "alice", "state": State("2026-04-14T10:00:00Z", [_s()])}]
    out = render.team_status_markdown(members)
    assert "| @alice |" in out
    assert "| 1 |" in out
    assert "main" in out


def test_render_markdown_unparseable():
    out = render.team_status_markdown(
        [{"login": "bob", "state": None, "issue_number": 7}],
    )
    assert "⚠" in out
    assert "#7" in out


def test_render_ansi_contains_data():
    members = [{"login": "alice", "state": State("2026-04-14T10:00:00Z", [_s(b="dev")])}]
    out = render.team_status_ansi(members)
    assert "alice" in out
    assert "dev" in out


def test_render_multi_branch_user():
    """Multiple sessions on different branches → branches joined."""
    a = _sess(branch="main")
    b = _sess(branch="feat/x", worktree_path="/w2")
    out = render.team_status_markdown([_member("alice", [a, b])])
    assert "@alice" in out
    assert "main" in out and "feat/x" in out
    assert "| 2 |" in out


def test_render_tolerates_legacy_kwargs():
    """Callers from older plugin versions may still pass stale_threshold
    and now_iso kwargs — renderer must accept and ignore them."""
    out = render.team_status_markdown(
        [_member("alice", [_sess()])],
        stale_threshold_seconds=900,
        now_iso="2026-04-14T11:00:00Z",
    )
    assert "@alice" in out
