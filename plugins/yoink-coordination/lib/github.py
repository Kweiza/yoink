"""gh CLI wrappers. All functions return None / [] on failure (non-raising).

Phase 5: every public wrapper threads its own name into `_run(..., caller=...)`.
`_run` emits a `[yoink-metric] api_error` line when the subprocess returns
non-zero OR raises TimeoutExpired. Emit location centralized so new wrappers
only need to remember the `caller=` kwarg (enforced by parametric test).
"""
from __future__ import annotations
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Dict, Optional


def _emit_api_error(call: str, status: int) -> None:
    # Lazy import to keep this module free of telemetry at import-time.
    from telemetry import emit
    emit("lib", "api_error", call=call, status=status)


def _run(args, input_text: Optional[str] = None, timeout: int = 20,
         caller: Optional[str] = None):
    try:
        r = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            input=input_text,
        )
    except subprocess.TimeoutExpired:
        if caller:
            _emit_api_error(caller, -1)
        raise
    if caller and r.returncode != 0:
        _emit_api_error(caller, r.returncode)
    return r


def label_exists(name: str) -> bool:
    r = _run(["gh", "label", "list", "--json", "name", "--limit", "200"],
             caller="label_exists")
    if r.returncode != 0:
        return False
    try:
        return any(l.get("name") == name for l in json.loads(r.stdout))
    except json.JSONDecodeError:
        return False


def create_label(name: str, color: str = "ededed", description: str = "") -> bool:
    r = _run(
        ["gh", "label", "create", name, "--color", color,
         "--description", description],
        caller="create_label",
    )
    return r.returncode == 0


def list_my_status_issues(login: str, label: str) -> List[Dict]:
    r = _run(
        ["gh", "issue", "list", "--label", label, "--state", "all",
         "--json", "number,state,assignees,body,updatedAt", "--limit", "200"],
        caller="list_my_status_issues",
    )
    if r.returncode != 0:
        return []
    try:
        all_issues = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []
    return [
        i for i in all_issues
        if any(a.get("login") == login for a in i.get("assignees", []))
    ]


def list_other_status_issues_open(login: str, label: str) -> List[Dict]:
    r = _run(
        ["gh", "issue", "list", "--label", label, "--state", "open",
         "--json", "number,state,assignees,body,updatedAt", "--limit", "200"],
        caller="list_other_status_issues_open",
    )
    if r.returncode != 0:
        return []
    try:
        all_issues = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []
    return [
        i for i in all_issues
        if i.get("assignees")
        and not any(a.get("login") == login for a in i["assignees"])
    ]


def create_status_issue(login: str, label: str) -> Optional[int]:
    title = f"[yoink-status] {login}"
    r = _run(
        ["gh", "issue", "create", "--title", title, "--body", "",
         "--label", label, "--assignee", login],
        caller="create_status_issue",
    )
    if r.returncode != 0:
        return None
    m = re.search(r"/issues/(\d+)", r.stdout)
    return int(m.group(1)) if m else None


def edit_issue_body(num: int, body: str) -> bool:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(body)
        tmp = f.name
    try:
        r = _run(["gh", "issue", "edit", str(num), "--body-file", tmp],
                 caller="edit_issue_body")
        return r.returncode == 0
    finally:
        Path(tmp).unlink(missing_ok=True)


def reopen_issue(num: int) -> bool:
    return _run(["gh", "issue", "reopen", str(num)], caller="reopen_issue").returncode == 0


def close_issue(num: int) -> bool:
    return _run(["gh", "issue", "close", str(num)], caller="close_issue").returncode == 0


def add_label(num: int, label: str) -> bool:
    return _run(
        ["gh", "issue", "edit", str(num), "--add-label", label],
        caller="add_label",
    ).returncode == 0


def remove_label(num: int, label: str) -> bool:
    return _run(
        ["gh", "issue", "edit", str(num), "--remove-label", label],
        caller="remove_label",
    ).returncode == 0


def gh_auth_ok() -> bool:
    # Intentionally NO caller= here: gh_auth_ok is a probe that callers use
    # to short-circuit when auth is missing. A failure is a normal control
    # flow signal, not an API error worth counting.
    return _run(["gh", "auth", "status"]).returncode == 0
