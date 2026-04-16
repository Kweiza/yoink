# tests/test_state.py
import state
from state import State, Session, parse_body, render_body, dedup_key

def _session(sid="s1", ws="/ws/a", branch="main", ccs=None):
    return Session(
        session_id=sid, worktree_path=ws, branch=branch, task_issue=None,
        started_at="2026-04-14T10:00:00Z",
        declared_files=[], driven_by="claude-code", claude_session_id=ccs,
    )

def test_parse_empty_body_returns_default_state():
    s, warn = parse_body("")
    assert s.sessions == []
    assert warn is False

def test_parse_body_with_valid_marker_extracts_sessions():
    body = render_body(State(updated_at="2026-04-14T10:00:00Z",
                             sessions=[_session()]), login="alice")
    s, warn = parse_body(body)
    assert len(s.sessions) == 1
    assert s.sessions[0].session_id == "s1"
    assert warn is False

def test_parse_corrupt_json_returns_default_and_warns():
    body = "<!-- yoink:state-json-v1:begin\n{not json}\nyoink:state-json-v1:end -->"
    s, warn = parse_body(body)
    assert s.sessions == []
    assert warn is True

def test_dedup_key_prefers_claude_session_id():
    assert dedup_key(_session(ccs="ccs-1")) == ("ccs", "ccs-1")
    assert dedup_key(_session(ccs=None)) == ("wb", "/ws/a", "main")

def test_render_preserves_human_tail():
    human = "\n\n---\nMy personal note\n"
    original = render_body(State("2026-04-14T10:00:00Z", []), login="alice") + human
    state2 = State("2026-04-14T10:01:00Z", [_session()])
    out = render_body(state2, login="alice", preserve_tail_from=original)
    assert "My personal note" in out

def test_render_includes_summary_and_table():
    body = render_body(State("2026-04-14T10:00:00Z", [_session()]), login="alice")
    assert "@alice" in body
    assert "| Worktree |" in body
    assert "main" in body

def test_body_size_warning_when_exceeds_limit():
    # 1000 sessions should exceed 65536
    many = [_session(sid=f"s{i}", ws=f"/ws/{i}", branch=f"b{i}") for i in range(1000)]
    body = render_body(State("2026-04-14T10:00:00Z", many), login="alice")
    from state import body_exceeds_limit
    assert body_exceeds_limit(body) is True

def test_parse_tolerates_unknown_session_fields():
    # Phase 3 may add fields; Phase 2 must not crash on them
    import json as _json
    body = (
        "<!-- yoink:state-json-v1:begin\n"
        + _json.dumps({"updated_at": "t", "sessions": [{
            "session_id": "s1", "worktree_path": "/w", "branch": "m",
            "task_issue": None, "started_at": "t", "last_heartbeat": "t",
            "declared_files": [], "driven_by": "claude-code", "claude_session_id": None,
            "future_phase3_field": {"any": "shape"},
        }]})
        + "\nyoink:state-json-v1:end -->"
    )
    s, warn = parse_body(body)
    assert warn is False
    assert len(s.sessions) == 1
    assert s.sessions[0].session_id == "s1"

def test_roundtrip_preserves_declared_files_field():
    # declared_files is reserved for Phase 3; Phase 2 must not drop it
    body = render_body(State("2026-04-14T10:00:00Z", [_session()]), login="alice")
    s, warn = parse_body(body)
    assert s.sessions[0].declared_files == []

def test_pluralization_single_session_uses_singular():
    body = render_body(State("2026-04-14T10:00:00Z", [_session()]), login="alice")
    # v0.3.28: summary no longer includes heartbeat, so the line ends
    # after "1 active session".
    assert "1 active session" in body
    assert "1 active sessions" not in body

def test_parse_malformed_one_session_keeps_others():
    # One session missing required field; other is valid
    raw = (
        "<!-- yoink:state-json-v1:begin\n"
        '{"updated_at":"t","sessions":['
        '{"session_id":""},'  # bad: empty session_id triggers __post_init__
        '{"session_id":"s2","worktree_path":"/w","branch":"m","task_issue":null,'
        '"started_at":"t","last_heartbeat":"t","declared_files":[],"driven_by":"claude-code","claude_session_id":null}'
        ']}\n'
        "yoink:state-json-v1:end -->"
    )
    s, warn = parse_body(raw)
    assert warn is False
    assert len(s.sessions) == 1
    assert s.sessions[0].session_id == "s2"

def test_roundtrip_preserves_unknown_phase3_fields():
    import json as _json
    raw = (
        "<!-- yoink:state-json-v1:begin\n"
        + _json.dumps({"updated_at": "t", "sessions": [{
            "session_id": "s1", "worktree_path": "/w", "branch": "m",
            "task_issue": None, "started_at": "t", "last_heartbeat": "t",
            "declared_files": [], "driven_by": "claude-code", "claude_session_id": None,
            "future_phase3_field": {"any": "shape"},
        }]})
        + "\nyoink:state-json-v1:end -->"
    )
    parsed, _ = parse_body(raw)
    re_rendered = render_body(parsed, login="alice")
    assert '"future_phase3_field":' in re_rendered.replace(" ", "") or '"future_phase3_field"' in re_rendered


def test_format_task_cell_issue_and_summary():
    from state import format_task_cell
    assert format_task_cell("kweiza/yoink#42", "Fix login flow") == "#42 · Fix login flow"


def test_format_task_cell_issue_only():
    from state import format_task_cell
    assert format_task_cell("repo#7", None) == "#7"


def test_format_task_cell_summary_only():
    from state import format_task_cell
    assert format_task_cell(None, "refactor auth") == "refactor auth"


def test_format_task_cell_neither():
    from state import format_task_cell
    assert format_task_cell(None, None) == "—"


def test_format_task_cell_long_summary_truncated():
    from state import format_task_cell
    out = format_task_cell(None, "x" * 100)
    assert out.endswith("…")
    assert len(out) <= 60


def test_format_files_cell_empty():
    from state import format_files_cell
    assert format_files_cell([]) == "—"
    assert format_files_cell([{"path": ""}]) == "—"


def test_format_files_cell_up_to_three():
    from state import format_files_cell
    items = [{"path": "a.py"}, {"path": "b.py"}, {"path": "c.py"}]
    assert format_files_cell(items) == "a.py, b.py, c.py"


def test_format_files_cell_overflow():
    from state import format_files_cell
    items = [{"path": f"f{i}.py"} for i in range(6)]
    assert format_files_cell(items) == "f0.py, f1.py, f2.py (+3)"


def test_render_body_includes_files_column_and_task_summary():
    from state import State, Session, render_body
    s = Session(
        session_id="s1", worktree_path="/tmp/r", branch="feat-42",
        task_issue="repo#42", started_at="2026-04-15T00:00:00Z",
        declared_files=[{"path": "a.py", "declared_at": "t"}],
        driven_by="claude-code", claude_session_id="ccs-1",
        task_summary="Add 2FA",
    )
    body = render_body(State(updated_at="now", sessions=[s]), login="alice")
    assert "| Worktree | Branch | Task | Files | Started |" in body
    assert "#42 · Add 2FA" in body
    assert "a.py" in body


def test_parse_body_roundtrips_task_summary():
    """task_summary set via render_body must come back through parse_body."""
    from state import State, Session, render_body, parse_body
    s = Session(
        session_id="s1", worktree_path="/tmp/r", branch="b",
        task_issue=None, started_at="2026-04-15T00:00:00Z",
        declared_files=[], driven_by="claude-code",
        claude_session_id="ccs-1", task_summary="Refactor auth",
    )
    body = render_body(State(updated_at="now", sessions=[s]), login="alice")
    parsed, _ = parse_body(body)
    assert parsed.sessions[0].task_summary == "Refactor auth"
