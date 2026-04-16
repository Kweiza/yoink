"""Render /team-status output. Markdown for Claude Code, ANSI for CLI.

v0.3.28: removed Heartbeat column + stale (⚠) markers. Actions handles
release automatically now, so "which session might be stale" is no longer
a useful client-side signal — sessions disappear from the issue body
when their declared_files land on primary, and that's the only surviving
meaning of "activity".
"""
from __future__ import annotations
from typing import List, Dict, Any


def _branches_cell(sessions) -> str:
    names = sorted({s.branch for s in sessions if s.branch})
    return ", ".join(names) if names else "—"


def _tasks_cell(sessions) -> str:
    tasks = sorted({s.task_issue for s in sessions if s.task_issue})
    return ", ".join(tasks) or "—"


def _user_cell(login: str, sessions) -> str:
    return f"@{login}"


def team_status_markdown(members: List[Dict[str, Any]], **_ignored) -> str:
    if not members:
        return "_No team members active in this repo._"
    lines = [
        "| User | Sessions | Branches | Task |",
        "|---|---|---|---|",
    ]
    warnings = []
    for m in members:
        st = m.get("state")
        if st is None:
            warnings.append(f"⚠ unparseable state for issue #{m.get('issue_number', '?')}")
            continue
        user = _user_cell(m["login"], st.sessions)
        branches = _branches_cell(st.sessions)
        tasks = _tasks_cell(st.sessions)
        lines.append(f"| {user} | {len(st.sessions)} | {branches} | {tasks} |")
    body = "\n".join(lines)
    if warnings:
        body += "\n\n" + "\n".join(warnings)
    return body


def team_status_ansi(members: List[Dict[str, Any]], **_ignored) -> str:
    if not members:
        return "(no team members active)"
    rows = [("USER", "SESSIONS", "BRANCHES", "TASK")]
    for m in members:
        st = m.get("state")
        if st is None:
            rows.append((m["login"], "?", "(unparseable)", ""))
            continue
        user = _user_cell(m["login"], st.sessions).lstrip("@")
        branches = _branches_cell(st.sessions)
        tasks = _tasks_cell(st.sessions)
        rows.append((user, str(len(st.sessions)), branches, tasks))
    widths = [max(len(str(r[i])) for r in rows) for i in range(4)]
    lines = []
    for i, row in enumerate(rows):
        line = "  ".join(str(row[j]).ljust(widths[j]) for j in range(4))
        lines.append(line)
        if i == 0:
            lines.append("  ".join("-" * widths[j] for j in range(4)))
    return "\n".join(lines)
