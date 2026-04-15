"""Unit tests for templates/github/yoink/release.py — the script that runs
inside the GitHub Action."""
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
    r._PRIMARY_HIT_CACHE.clear()
    return r


def _session_dict(path="src/foo.py", declared_at="2026-04-15T10:00:00Z",
                  claude_session_id="ccs"):
    return {
        "session_id": "s",
        "worktree_path": "/wt",
        "branch": "feature",
        "task_issue": None,
        "started_at": "2026-04-15T10:00:00Z",
        "last_heartbeat": "2026-04-15T10:00:00Z",
        "declared_files": [{"path": path, "declared_at": declared_at}],
        "driven_by": "claude-code",
        "claude_session_id": claude_session_id,
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


def _parse_sessions(body, release_mod):
    parsed, _ = release_mod.state_mod.parse_body(body)
    return parsed.sessions


def test_release_drops_path_committed_on_primary(release_mod):
    s_dict = _session_dict("src/foo.py")
    session = _parse_sessions(_body({"updated_at": "", "sessions": [s_dict]}),
                              release_mod)[0]
    with patch.object(release_mod, "_committed_on_primary_since",
                      return_value=True):
        assert release_mod._release_in_session(session, "main") is True
    assert session.declared_files == []


def test_release_keeps_path_not_yet_on_primary(release_mod):
    s_dict = _session_dict("src/foo.py")
    session = _parse_sessions(_body({"updated_at": "", "sessions": [s_dict]}),
                              release_mod)[0]
    with patch.object(release_mod, "_committed_on_primary_since",
                      return_value=False):
        assert release_mod._release_in_session(session, "main") is False
    assert len(session.declared_files) == 1


def test_committed_on_primary_since_positive(release_mod):
    """When git log returns a commit, the helper returns True."""
    with patch.object(release_mod, "_run", return_value="abc123\n"):
        assert release_mod._committed_on_primary_since(
            "main", "src/foo.py", "2026-04-15T10:00:00Z"
        ) is True


def test_committed_on_primary_since_negative(release_mod):
    with patch.object(release_mod, "_run", return_value=""):
        assert release_mod._committed_on_primary_since(
            "main", "src/foo.py", "2026-04-15T10:00:00Z"
        ) is False


def test_process_issue_no_change_when_nothing_landed(release_mod):
    issue = {"number": 7,
             "body": _body({"updated_at": "", "sessions": [_session_dict()]}),
             "assignees": [{"login": "alice"}]}
    with patch.object(release_mod, "_committed_on_primary_since",
                      return_value=False), \
         patch.object(release_mod, "_run") as run_mock:
        result = release_mod._process_issue("o/r", issue, "main")
    assert result == "no-change"
    run_mock.assert_not_called()


def test_process_issue_edits_when_some_paths_release(release_mod):
    """Session has two paths; one is now on main, the other still pending
    → edit body, keep issue open."""
    s = _session_dict()
    s["declared_files"] = [
        {"path": "merged.py", "declared_at": "2026-04-15T10:00:00Z"},
        {"path": "pending.py", "declared_at": "2026-04-15T10:00:00Z"},
    ]
    issue = {"number": 7,
             "body": _body({"updated_at": "", "sessions": [s]}),
             "assignees": [{"login": "alice"}]}

    def stub_committed(primary, path, since):
        return path == "merged.py"

    with patch.object(release_mod, "_committed_on_primary_since",
                      side_effect=stub_committed), \
         patch.object(release_mod, "_run") as run_mock:
        result = release_mod._process_issue("o/r", issue, "main")
    assert result == "edited"
    edit_calls = [c for c in run_mock.call_args_list
                  if c.args[0][:4] == ["gh", "issue", "edit", "7"]]
    assert len(edit_calls) == 1
    body_arg = edit_calls[0].args[0][-1]
    assert "pending.py" in body_arg
    assert '"path": "merged.py"' not in body_arg


def test_process_issue_closes_when_all_released(release_mod):
    issue = {"number": 7,
             "body": _body({"updated_at": "", "sessions": [_session_dict("only.py")]}),
             "assignees": [{"login": "alice"}]}
    with patch.object(release_mod, "_committed_on_primary_since",
                      return_value=True), \
         patch.object(release_mod, "_run") as run_mock:
        result = release_mod._process_issue("o/r", issue, "main")
    assert result == "closed"
    cmds = [c.args[0] for c in run_mock.call_args_list]
    assert any(c[:3] == ["gh", "issue", "edit"] and "--body" in c for c in cmds)
    assert any(c[:3] == ["gh", "issue", "close"] for c in cmds)


def test_process_issue_sweeps_stale_entry_from_prior_push(release_mod):
    """Regression for the user-reported bug: an entry whose path was
    merged in a PRIOR push (where the Action failed or wasn't installed)
    must be released in a subsequent Action run. The current push's
    diff is irrelevant — only `origin/<primary>` presence after
    declared_at matters."""
    s = _session_dict("old.py", declared_at="2026-04-10T00:00:00Z")
    issue = {"number": 7,
             "body": _body({"updated_at": "", "sessions": [s]}),
             "assignees": [{"login": "alice"}]}

    # The merge commit for old.py happened a while back, but sweep catches it.
    with patch.object(release_mod, "_committed_on_primary_since",
                      return_value=True), \
         patch.object(release_mod, "_run"):
        result = release_mod._process_issue("o/r", issue, "main")
    assert result == "closed"


def test_process_issue_drops_one_session_keeps_other(release_mod):
    s1 = _session_dict("only.py", claude_session_id="ccs-1")
    s2 = _session_dict("kept.py", claude_session_id="ccs-2")
    issue = {"number": 7,
             "body": _body({"updated_at": "", "sessions": [s1, s2]}),
             "assignees": [{"login": "alice"}]}

    def stub_committed(primary, path, since):
        return path == "only.py"

    with patch.object(release_mod, "_committed_on_primary_since",
                      side_effect=stub_committed), \
         patch.object(release_mod, "_run") as run_mock:
        result = release_mod._process_issue("o/r", issue, "main")
    assert result == "edited"
    cmds = [c.args[0] for c in run_mock.call_args_list]
    assert all(c[:3] != ["gh", "issue", "close"] for c in cmds)
    edit_call = next(c for c in cmds if c[:3] == ["gh", "issue", "edit"])
    assert "ccs-1" not in edit_call[-1]
    assert "ccs-2" in edit_call[-1]
