"""Unit tests for templates/github/yoink/release.py — the script that runs
inside the GitHub Action. Tests the pure logic (release-in-session, issue
processing decision) by importing the script as a module and stubbing
`gh` / git subprocess calls."""
from __future__ import annotations
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def release_mod():
    tpl = Path(__file__).resolve().parents[1] / "templates" / "github" / "yoink"
    sys.path.insert(0, str(tpl))
    import release as r  # noqa
    importlib.reload(r)
    return r


def _session_dict(path="src/foo.py"):
    return {
        "session_id": "s",
        "worktree_path": "/wt",
        "branch": "feature",
        "task_issue": None,
        "started_at": "2026-04-15T10:00:00Z",
        "last_heartbeat": "2026-04-15T10:00:00Z",
        "declared_files": [{"path": path, "declared_at": "2026-04-15T10:00:00Z"}],
        "driven_by": "claude-code",
        "claude_session_id": "ccs",
    }


def _body(state_dict):
    state_json = json.dumps(state_dict, indent=2)
    return (
        "summary stuff\n\n"
        "<!-- yoink:state-json-v1:begin\n"
        + state_json + "\n"
        "yoink:state-json-v1:end -->\n"
        "tail prose"
    )


def test_release_in_session_drops_matching_path(release_mod):
    s_dict = _session_dict("src/foo.py")
    parsed, _ = release_mod.state_mod.parse_body(_body(
        {"updated_at": "", "sessions": [s_dict]}
    ))
    session = parsed.sessions[0]
    changed = {"src/foo.py", "other.txt"}
    assert release_mod._release_in_session(session, changed) is True
    assert session.declared_files == []


def test_release_in_session_keeps_unmerged(release_mod):
    s_dict = _session_dict("src/foo.py")
    parsed, _ = release_mod.state_mod.parse_body(_body(
        {"updated_at": "", "sessions": [s_dict]}
    ))
    session = parsed.sessions[0]
    changed = {"unrelated.txt"}
    assert release_mod._release_in_session(session, changed) is False
    assert len(session.declared_files) == 1


def test_process_issue_no_change_when_nothing_matches(release_mod):
    issue = {"number": 7, "body": _body({"updated_at": "", "sessions": [_session_dict()]}),
             "assignees": [{"login": "alice"}]}
    with patch.object(release_mod, "_run") as run_mock:
        result = release_mod._process_issue("o/r", issue, {"unrelated.py"})
    assert result == "no-change"
    run_mock.assert_not_called()


def test_process_issue_edits_when_some_paths_release(release_mod):
    """Two declared paths; one merged, one still unmerged → body edited,
    issue stays open."""
    s = _session_dict()
    s["declared_files"] = [
        {"path": "merged.py", "declared_at": "2026-04-15T10:00:00Z"},
        {"path": "unmerged.py", "declared_at": "2026-04-15T10:00:00Z"},
    ]
    issue = {"number": 7, "body": _body({"updated_at": "", "sessions": [s]}),
             "assignees": [{"login": "alice"}]}
    with patch.object(release_mod, "_run") as run_mock:
        result = release_mod._process_issue("o/r", issue, {"merged.py"})
    assert result == "edited"
    edit_calls = [c for c in run_mock.call_args_list
                  if c.args[0][:4] == ["gh", "issue", "edit", "7"]]
    assert len(edit_calls) == 1
    body_arg = edit_calls[0].args[0][-1]
    assert "unmerged.py" in body_arg
    assert "\"path\": \"merged.py\"" not in body_arg


def test_process_issue_closes_when_all_released(release_mod):
    """Single session, single declared path → fully merged → close issue."""
    issue = {"number": 7, "body": _body({"updated_at": "", "sessions": [_session_dict("only.py")]}),
             "assignees": [{"login": "alice"}]}
    with patch.object(release_mod, "_run") as run_mock:
        result = release_mod._process_issue("o/r", issue, {"only.py"})
    assert result == "closed"
    cmds = [c.args[0] for c in run_mock.call_args_list]
    # Must call edit (body), remove-label (active), close
    assert any(c[:3] == ["gh", "issue", "edit"] and "--body" in c for c in cmds)
    assert any(c[:3] == ["gh", "issue", "close"] for c in cmds)


def test_process_issue_drops_session_when_its_files_all_release(release_mod):
    """One session fully released, one keeps a path → that session pops,
    issue stays open with the surviving session."""
    s1 = _session_dict("only.py")
    s1["claude_session_id"] = "ccs-1"
    s2 = _session_dict("kept.py")
    s2["claude_session_id"] = "ccs-2"
    issue = {"number": 7, "body": _body({"updated_at": "", "sessions": [s1, s2]}),
             "assignees": [{"login": "alice"}]}
    with patch.object(release_mod, "_run") as run_mock:
        result = release_mod._process_issue("o/r", issue, {"only.py"})
    assert result == "edited"
    # close should NOT have been called
    cmds = [c.args[0] for c in run_mock.call_args_list]
    assert all(c[:3] != ["gh", "issue", "close"] for c in cmds)
    # body must drop ccs-1 entry
    edit_call = next(c for c in cmds if c[:3] == ["gh", "issue", "edit"])
    assert "ccs-1" not in edit_call[-1]
    assert "ccs-2" in edit_call[-1]
