"""Render /team-status output. Markdown for Claude Code, ANSI for CLI.

Phase 4: stale `⚠` indicator + config via keyword-only parameters (pure module).
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional


def _parse_iso_utc(s: str) -> Optional[datetime]:
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _is_stale(session, threshold: timedelta, now: datetime) -> bool:
    hb = _parse_iso_utc(getattr(session, "last_heartbeat", "") or getattr(session, "started_at", ""))
    if hb is None:
        return False  # per-session fail-safe
    return (now - hb) > threshold


def _compute_stale_map(sessions, threshold_seconds: int, now_iso: str) -> Dict[int, bool]:
    """Return {id(session): is_stale_bool}. Fail-safe on bad now_iso."""
    now = _parse_iso_utc(now_iso)
    if now is None:
        return {id(s): False for s in sessions}
    threshold = timedelta(seconds=threshold_seconds)
    return {id(s): _is_stale(s, threshold, now) for s in sessions}


def _branches_cell(sessions, stale_map: Dict[int, bool]) -> str:
    """Stable sorted unique branch names, stale branches get ⚠ suffix."""
    # Track per-branch: if ANY session on that branch is stale, flag the branch.
    per_branch_stale: Dict[str, bool] = {}
    for s in sessions:
        per_branch_stale[s.branch] = per_branch_stale.get(s.branch, False) or stale_map.get(id(s), False)
    if not per_branch_stale:
        return "—"
    parts = []
    for br in sorted(per_branch_stale):
        parts.append(f"{br} ⚠" if per_branch_stale[br] else br)
    return ", ".join(parts)


def _tasks_cell(sessions) -> str:
    tasks = sorted({s.task_issue for s in sessions if s.task_issue})
    return ", ".join(tasks) or "—"


def _freshest_heartbeat(sessions) -> str:
    return max((s.last_heartbeat for s in sessions), default="—")


def _user_cell(login: str, sessions, stale_map: Dict[int, bool]) -> str:
    any_stale = any(stale_map.get(id(s), False) for s in sessions)
    base = f"@{login}"
    return f"{base} ⚠" if any_stale else base


def team_status_markdown(
    members: List[Dict[str, Any]],
    *,
    stale_threshold_seconds: int,
    now_iso: str,
) -> str:
    """Render member list as a Markdown table. Stale sessions get ⚠ markers
    per spec §6.1: user cell + branch cell (individual branch), freshest
    heartbeat preserved in Last heartbeat column."""
    if not members:
        return "_No team members active in this repo._"
    lines = [
        "| User | Sessions | Branches | Task | Last heartbeat |",
        "|---|---|---|---|---|",
    ]
    warnings = []
    for m in members:
        st = m.get("state")
        if st is None:
            warnings.append(f"⚠ unparseable state for issue #{m.get('issue_number', '?')}")
            continue
        stale_map = _compute_stale_map(st.sessions, stale_threshold_seconds, now_iso)
        user = _user_cell(m["login"], st.sessions, stale_map)
        branches = _branches_cell(st.sessions, stale_map)
        tasks = _tasks_cell(st.sessions)
        last = _freshest_heartbeat(st.sessions)
        lines.append(f"| {user} | {len(st.sessions)} | {branches} | {tasks} | {last} |")
    body = "\n".join(lines)
    if warnings:
        body += "\n\n" + "\n".join(warnings)
    return body


def team_status_ansi(
    members: List[Dict[str, Any]],
    *,
    stale_threshold_seconds: int,
    now_iso: str,
) -> str:
    """Plain-text table for terminal output. Same stale rules as markdown."""
    if not members:
        return "(no team members active)"
    rows = [("USER", "SESSIONS", "BRANCHES", "TASK", "HEARTBEAT")]
    for m in members:
        st = m.get("state")
        if st is None:
            rows.append((m["login"], "?", "(unparseable)", "", ""))
            continue
        stale_map = _compute_stale_map(st.sessions, stale_threshold_seconds, now_iso)
        user = _user_cell(m["login"], st.sessions, stale_map).lstrip("@")  # ANSI: drop leading @
        branches = _branches_cell(st.sessions, stale_map)
        tasks = _tasks_cell(st.sessions)
        last = _freshest_heartbeat(st.sessions)
        rows.append((user, str(len(st.sessions)), branches, tasks, last))
    widths = [max(len(str(r[i])) for r in rows) for i in range(5)]
    lines = []
    for i, row in enumerate(rows):
        line = "  ".join(str(row[j]).ljust(widths[j]) for j in range(5))
        lines.append(line)
        if i == 0:
            lines.append("  ".join("-" * widths[j] for j in range(5)))
    return "\n".join(lines)
