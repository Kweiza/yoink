"""Conflict warning text rendering (spec §5.2).

`format_conflict` returns a multi-line string suitable for stderr. Per Task 0 D
(journal 17), exit-0 hook stdout/stderr is NOT delivered to Claude context in
v2.1.105, so this text is user-terminal-only for advisory mode. Block mode
relies on the exit 2 + stderr path (tested in E2E T16).
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Dict, Optional


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def format_rel(earlier: str, now: str) -> str:
    e = _parse_iso(earlier)
    n = _parse_iso(now)
    if e is None or n is None or n < e:
        return "?"
    secs = int((n - e).total_seconds())
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _owner_line(o: Dict, now_iso: str) -> str:
    login = o.get("login", "?")
    branch = o.get("branch", "?")
    rel = format_rel(o.get("declared_at", ""), now_iso)
    task = o.get("task_issue") or "—"
    return f"        @{login} · branch: {branch} · {rel} ago · task: {task}"


def format_conflict(*, path: str, owners: List[Dict], mode: str, now_iso: str) -> str:
    """Return a multi-line warning for stderr (and Claude context if mode=block
    and caller uses exit 2)."""
    sorted_owners = sorted(owners, key=lambda o: o.get("declared_at", ""))
    head_login = sorted_owners[0].get("login", "?") if sorted_owners else "?"
    lines = [
        f"[yoink] ⚠ {path} claimed by @{head_login}"
        + (f" (+{len(sorted_owners)-1} more)" if len(sorted_owners) > 1 else ""),
    ]
    for o in sorted_owners:
        lines.append(_owner_line(o, now_iso))
    if mode == "block":
        lines.append(
            "        mode: block — ask the claim-holder to commit, "
            "or set conflict_mode=advisory"
        )
    else:
        lines.append(
            "        mode: advisory — proceeding (set conflict_mode=block to enforce)"
        )
    return "\n".join(lines)
