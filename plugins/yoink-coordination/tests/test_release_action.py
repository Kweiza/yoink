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
    r._BRANCH_READY_CACHE.clear()
    r._SYNCED_CACHE.clear()
    return r


def _session_dict(path="src/foo.py", declared_at="2026-04-15T10:00:00Z",
                  claude_session_id="ccs", branch="feature"):
    return {
        "session_id": "s",
        "worktree_path": "/wt",
        "branch": branch,
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


# ── _should_release dispatching ─────────────────────────────────

def test_should_release_uses_content_diff_for_feature_branch(release_mod):
    """Non-primary session → content diff path."""
    with patch.object(release_mod, "_ensure_remote_branch", return_value=True), \
         patch.object(release_mod, "_path_synced_with_primary",
                      return_value=True) as synced_mock, \
         patch.object(release_mod, "_committed_on_primary_since") as commit_mock:
        assert release_mod._should_release("main", "feature", "a.py",
                                           "2026-04-15T00:00:00Z") is True
    synced_mock.assert_called_once()
    commit_mock.assert_not_called()


def test_should_release_falls_back_to_commit_check_when_primary_session(release_mod):
    """Session on primary itself → commit check path."""
    with patch.object(release_mod, "_path_synced_with_primary") as synced_mock, \
         patch.object(release_mod, "_committed_on_primary_since",
                      return_value=True) as commit_mock:
        assert release_mod._should_release("main", "main", "a.py",
                                           "2026-04-15T00:00:00Z") is True
    synced_mock.assert_not_called()
    commit_mock.assert_called_once()


def test_should_release_falls_back_when_branch_missing(release_mod):
    """Feature branch gone from origin (deleted after merge) → commit check."""
    with patch.object(release_mod, "_ensure_remote_branch", return_value=False), \
         patch.object(release_mod, "_committed_on_primary_since",
                      return_value=True) as commit_mock:
        assert release_mod._should_release("main", "deleted-branch", "a.py",
                                           "2026-04-15T00:00:00Z") is True
    commit_mock.assert_called_once()


# ── _release_in_session integration with _should_release ────────

def test_release_drops_path_synced_with_primary(release_mod):
    s_dict = _session_dict("src/foo.py")
    session = _parse_sessions(_body({"updated_at": "", "sessions": [s_dict]}),
                              release_mod)[0]
    with patch.object(release_mod, "_should_release", return_value=True):
        assert release_mod._release_in_session(session, "main") is True
    assert session.declared_files == []


def test_release_keeps_path_when_not_synced(release_mod):
    s_dict = _session_dict("src/foo.py")
    session = _parse_sessions(_body({"updated_at": "", "sessions": [s_dict]}),
                              release_mod)[0]
    with patch.object(release_mod, "_should_release", return_value=False):
        assert release_mod._release_in_session(session, "main") is False
    assert len(session.declared_files) == 1


# ── _committed_on_primary_since ─────────────────────────────────

def test_committed_on_primary_since_positive(release_mod):
    with patch.object(release_mod, "_run", return_value="abc123\n"):
        assert release_mod._committed_on_primary_since(
            "main", "src/foo.py", "2026-04-15T10:00:00Z"
        ) is True


def test_committed_on_primary_since_negative(release_mod):
    with patch.object(release_mod, "_run", return_value=""):
        assert release_mod._committed_on_primary_since(
            "main", "src/foo.py", "2026-04-15T10:00:00Z"
        ) is False


# ── _process_issue end-to-end ───────────────────────────────────

def test_process_issue_no_change_when_nothing_landed(release_mod):
    issue = {"number": 7,
             "body": _body({"updated_at": "", "sessions": [_session_dict()]}),
             "assignees": [{"login": "alice"}]}
    with patch.object(release_mod, "_should_release", return_value=False), \
         patch.object(release_mod, "_run") as run_mock:
        result = release_mod._process_issue("o/r", issue, "main")
    assert result == "no-change"
    run_mock.assert_not_called()


def test_process_issue_edits_when_some_paths_release(release_mod):
    s = _session_dict()
    s["declared_files"] = [
        {"path": "merged.py", "declared_at": "2026-04-15T10:00:00Z"},
        {"path": "pending.py", "declared_at": "2026-04-15T10:00:00Z"},
    ]
    issue = {"number": 7,
             "body": _body({"updated_at": "", "sessions": [s]}),
             "assignees": [{"login": "alice"}]}

    def stub(primary, branch, path, declared_at):
        return path == "merged.py"

    with patch.object(release_mod, "_should_release", side_effect=stub), \
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
    with patch.object(release_mod, "_should_release", return_value=True), \
         patch.object(release_mod, "_run") as run_mock:
        result = release_mod._process_issue("o/r", issue, "main")
    assert result == "closed"
    cmds = [c.args[0] for c in run_mock.call_args_list]
    assert any(c[:3] == ["gh", "issue", "edit"] and "--body" in c for c in cmds)
    assert any(c[:3] == ["gh", "issue", "close"] for c in cmds)


def test_process_issue_sweeps_squash_merge_net_zero(release_mod):
    """Regression for user-reported case: file added+deleted on feature
    branch, squash-merged to main. Primary has no commit touching the
    path, so commit-based check would miss it — but content-diff between
    origin/<branch> and origin/<primary> is empty (neither has the
    file), so _should_release returns True via the sync path."""
    s = _session_dict("temp.md", branch="yoink-test")
    issue = {"number": 7,
             "body": _body({"updated_at": "", "sessions": [s]}),
             "assignees": [{"login": "alice"}]}

    with patch.object(release_mod, "_ensure_remote_branch", return_value=True), \
         patch.object(release_mod, "_path_synced_with_primary",
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

    def stub(primary, branch, path, declared_at):
        return path == "only.py"

    with patch.object(release_mod, "_should_release", side_effect=stub), \
         patch.object(release_mod, "_run") as run_mock:
        result = release_mod._process_issue("o/r", issue, "main")
    assert result == "edited"
    cmds = [c.args[0] for c in run_mock.call_args_list]
    assert all(c[:3] != ["gh", "issue", "close"] for c in cmds)
    edit_call = next(c for c in cmds if c[:3] == ["gh", "issue", "edit"])
    assert "ccs-1" not in edit_call[-1]
    assert "ccs-2" in edit_call[-1]
