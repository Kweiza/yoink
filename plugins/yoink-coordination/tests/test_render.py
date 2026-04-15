import render
import state as state_mod
from state import State, Session


def _s(ws="/p/a", b="main", ti=None, hb="2026-04-14T10:00:00Z"):
    return Session("sid", ws, b, ti, hb, hb, [], "claude-code", None)


def _sess(branch="main", last_heartbeat="2026-04-14T10:59:00Z",
          task_issue=None, claude_session_id=None, worktree_path="/w"):
    return state_mod.Session(
        session_id="s-" + branch,
        worktree_path=worktree_path,
        branch=branch,
        task_issue=task_issue,
        started_at="2026-04-14T10:00:00Z",
        last_heartbeat=last_heartbeat,
        declared_files=[],
        driven_by="claude-code",
        claude_session_id=claude_session_id,
    )


def _member(login, sessions):
    return {"login": login, "issue_number": 1,
            "state": state_mod.State(updated_at="", sessions=sessions)}


# --- pre-existing Phase 2/3 tests (updated with required kwargs) ---

def test_render_markdown_empty():
    out = render.team_status_markdown([], stale_threshold_seconds=900, now_iso="2026-04-14T00:00:00Z")
    assert "No team members active" in out or "no team members active" in out.lower()


def test_render_markdown_one_user():
    members = [{"login": "alice", "state": State("2026-04-14T10:00:00Z", [_s()])}]
    out = render.team_status_markdown(members, stale_threshold_seconds=900, now_iso="2026-04-14T00:00:00Z")
    assert "| @alice |" in out
    assert "| 1 |" in out
    assert "main" in out


def test_render_markdown_unparseable():
    out = render.team_status_markdown(
        [{"login": "bob", "state": None, "issue_number": 7}],
        stale_threshold_seconds=900, now_iso="2026-04-14T00:00:00Z",
    )
    assert "⚠" in out
    assert "#7" in out


def test_render_ansi_contains_data():
    members = [{"login": "alice", "state": State("2026-04-14T10:00:00Z", [_s(b="dev")])}]
    out = render.team_status_ansi(members, stale_threshold_seconds=900, now_iso="2026-04-14T00:00:00Z")
    assert "alice" in out
    assert "dev" in out


# --- Phase 4 stale indicator tests ---

NOW = "2026-04-14T11:00:00Z"
STALE_THRESHOLD = 900  # 15 min


def test_markdown_no_stale_no_warning():
    alice_fresh = _sess(branch="main", last_heartbeat="2026-04-14T10:59:00Z")
    out = render.team_status_markdown(
        [_member("alice", [alice_fresh])],
        stale_threshold_seconds=STALE_THRESHOLD, now_iso=NOW,
    )
    assert "@alice" in out
    assert "⚠" not in out


def test_markdown_user_and_branch_flagged_when_any_stale():
    stale = _sess(branch="feature/login", last_heartbeat="2026-04-14T09:00:00Z")  # 2h ago
    fresh = _sess(branch="main", last_heartbeat="2026-04-14T10:59:00Z")
    out = render.team_status_markdown(
        [_member("alice", [stale, fresh])],
        stale_threshold_seconds=STALE_THRESHOLD, now_iso=NOW,
    )
    assert "@alice ⚠" in out
    assert "feature/login ⚠" in out
    # fresh branch not flagged
    assert "main" in out
    lines = [ln for ln in out.split("\n") if "@alice" in ln]
    assert "main ⚠" not in lines[0]  # only the stale branch marked


def test_markdown_freshest_heartbeat_preserved():
    stale = _sess(branch="a", last_heartbeat="2026-04-14T09:00:00Z")
    fresh = _sess(branch="b", last_heartbeat="2026-04-14T10:59:00Z")
    out = render.team_status_markdown(
        [_member("alice", [stale, fresh])],
        stale_threshold_seconds=STALE_THRESHOLD, now_iso=NOW,
    )
    # Freshest value still shown in last-heartbeat column
    assert "2026-04-14T10:59:00Z" in out


def test_markdown_malformed_timestamp_not_flagged_stale():
    bad = _sess(branch="weird", last_heartbeat="not-iso")
    out = render.team_status_markdown(
        [_member("alice", [bad])],
        stale_threshold_seconds=STALE_THRESHOLD, now_iso=NOW,
    )
    # Per-session fail-safe: malformed → not stale → no ⚠
    assert "⚠" not in out


def test_ansi_user_and_branch_flagged():
    stale = _sess(branch="feat/x", last_heartbeat="2026-04-14T09:00:00Z")
    fresh = _sess(branch="main", last_heartbeat="2026-04-14T10:59:00Z")
    out = render.team_status_ansi(
        [_member("bob", [stale, fresh])],
        stale_threshold_seconds=STALE_THRESHOLD, now_iso=NOW,
    )
    assert "bob ⚠" in out
    assert "feat/x ⚠" in out
