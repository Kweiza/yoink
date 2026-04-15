from unittest.mock import patch, MagicMock
import json
import github

def _mock_run(stdout="", returncode=0):
    r = MagicMock(); r.stdout = stdout; r.returncode = returncode; r.stderr = ""
    return r

def test_label_exists_true():
    payload = json.dumps([{"name": "yoink:status"}, {"name": "bug"}])
    with patch("github.subprocess.run", return_value=_mock_run(payload)):
        assert github.label_exists("yoink:status") is True

def test_label_exists_false():
    payload = json.dumps([{"name": "bug"}])
    with patch("github.subprocess.run", return_value=_mock_run(payload)):
        assert github.label_exists("yoink:status") is False

def test_list_my_status_issues_filters_by_assignee():
    payload = json.dumps([
        {"number": 1, "state": "OPEN", "assignees": [{"login": "alice"}], "body": "A"},
        {"number": 2, "state": "OPEN", "assignees": [{"login": "bob"}], "body": "B"},
    ])
    with patch("github.subprocess.run", return_value=_mock_run(payload)):
        issues = github.list_my_status_issues("alice", "yoink:status")
    assert len(issues) == 1
    assert issues[0]["number"] == 1

def test_list_other_status_issues_open_excludes_me():
    payload = json.dumps([
        {"number": 1, "state": "OPEN", "assignees": [{"login": "alice"}], "body": "A"},
        {"number": 2, "state": "OPEN", "assignees": [{"login": "bob"}], "body": "B"},
        {"number": 3, "state": "OPEN", "assignees": [], "body": "C"},
    ])
    with patch("github.subprocess.run", return_value=_mock_run(payload)):
        issues = github.list_other_status_issues_open("alice", "yoink:status")
    assert len(issues) == 1
    assert issues[0]["number"] == 2

def test_create_status_issue_returns_number():
    with patch("github.subprocess.run", return_value=_mock_run("https://github.com/o/r/issues/42\n")):
        num = github.create_status_issue("alice", "yoink:status")
    assert num == 42

def test_edit_issue_body_writes_tempfile():
    with patch("github.subprocess.run", return_value=_mock_run()) as m:
        github.edit_issue_body(5, "hello")
        call = m.call_args_list[0]
        args = call.args[0]
        assert "gh" == args[0] and "issue" in args and "edit" in args


# ------------------------------------------------------------------
# Phase 5 M3: api_error emit coverage
# ------------------------------------------------------------------
import inspect
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))
import github as gh_mod  # noqa: E402


def _public_functions() -> list:
    """Return (name, fn) for every public function defined in lib/github.py."""
    return [
        (name, fn)
        for name, fn in inspect.getmembers(gh_mod, inspect.isfunction)
        if not name.startswith("_") and fn.__module__ == gh_mod.__name__
    ]


def _synth_args(fn) -> tuple:
    """Synthesize safe positional args matching fn's signature."""
    sig = inspect.signature(fn)
    args = []
    for p in sig.parameters.values():
        if p.default is not inspect.Parameter.empty:
            continue
        ann = p.annotation
        if ann is int:
            args.append(0)
        elif ann in (str, inspect.Parameter.empty):
            args.append("x")
        else:
            args.append("x")
    return tuple(args)


def test_all_public_github_functions_emit_api_error_on_returncode_failure(capsys, monkeypatch):
    class FakeResult:
        returncode = 99
        stdout = ""
        stderr = ""

    def fake_run(*a, **kw):
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)

    for name, fn in _public_functions():
        try:
            fn(*_synth_args(fn))
        except Exception:
            pass

    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if ln.startswith("[yoink-metric] ")]
    seen_calls = set()
    for ln in lines:
        m = re.search(r'"metric":"api_error".*?"call":"([^"]+)"', ln)
        if m:
            seen_calls.add(m.group(1))

    expected = {name for name, _ in _public_functions()} - {"gh_auth_ok"}
    missing = expected - seen_calls
    assert not missing, (
        f"Public functions in lib/github.py missing api_error emit: {missing}. "
        "Every public gh wrapper except gh_auth_ok must pass caller=<name> "
        "into _run(). gh_auth_ok is a probe, not an API error source."
    )


def test_api_error_emit_includes_status(capsys, monkeypatch):
    class FakeResult:
        returncode = 42
        stdout = ""
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    gh_mod.label_exists("whatever")

    err = capsys.readouterr().err
    assert '"metric":"api_error"' in err
    assert '"call":"label_exists"' in err
    assert '"status":42' in err


def test_api_error_on_timeout_expired(capsys, monkeypatch):
    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=a[0] if a else "gh", timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        gh_mod.label_exists("x")
    except subprocess.TimeoutExpired:
        pass

    err = capsys.readouterr().err
    assert '"metric":"api_error"' in err
    assert '"call":"label_exists"' in err
    assert '"status":-1' in err
