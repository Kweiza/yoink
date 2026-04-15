"""In-memory operations on a Session's `declared_files` list.

All functions are pure: no git, no gh, no lock. Hook code composes these
into a single body edit (spec §3.1 step 11).

Entry shape (spec §4.1): {"path": "<rel-posix>", "declared_at": "<iso-z>"}.
Unknown fields on existing entries (e.g., a future `reason`) are preserved
verbatim — Phase 2 forward-compat principle applied at entry granularity.
"""
from __future__ import annotations
from typing import List, Dict, Iterable, Optional, Set, Tuple


def _paths(declared: Iterable[Dict]) -> List[str]:
    return [e.get("path", "") for e in declared]


def acquire(declared: List[Dict], path: str, now: str) -> Tuple[List[Dict], bool]:
    """Return (new_list, changed).

    If `path` is already claimed, return (list(declared), False).
    Otherwise append `{"path": path, "declared_at": now}` and return (new, True).
    """
    if path in _paths(declared):
        return list(declared), False
    return list(declared) + [{"path": path, "declared_at": now}], True


def self_cleanup(
    declared: List[Dict],
    dirty_paths: Optional[Set[str]],
) -> Tuple[List[Dict], List[str]]:
    """Keep only entries whose path is in `dirty_paths`.

    If `dirty_paths is None` (git status failed / non-repo), keep everything
    and return (list(declared), []) — per spec §3.4 "skip self-cleanup".
    """
    if dirty_paths is None:
        return list(declared), []
    kept = [e for e in declared if e.get("path") in dirty_paths]
    removed = [e.get("path", "") for e in declared if e.get("path") not in dirty_paths]
    return kept, removed


def release(
    declared: List[Dict],
    committed_paths: Set[str],
) -> Tuple[List[Dict], List[str]]:
    """Remove entries whose path is in `committed_paths`."""
    kept = [e for e in declared if e.get("path") not in committed_paths]
    removed = [e.get("path", "") for e in declared if e.get("path") in committed_paths]
    return kept, removed


from datetime import datetime, timedelta, timezone


def _parse_iso_utc(s):
    """Return datetime or None on malformed input. Inherits Phase 3 warning.py pattern."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def find_stale_sessions(sessions, now_iso, threshold_seconds):
    """Return the subset of `sessions` whose heartbeat is older than threshold.

    Heartbeat source: session.last_heartbeat (preferred); falls back to
    session.started_at if last_heartbeat is empty/null.

    **Per-session fail-safe**: if parsing a given session's timestamp fails
    (ISO malformed, both fields empty, etc.), that session is EXCLUDED from
    the returned list — conservatively treated as "not stale" so we never
    accidentally remove an entry we can't judge. The function itself does
    not raise; other sessions in the same call are evaluated normally.
    """
    now = _parse_iso_utc(now_iso)
    if now is None:
        return []  # can't evaluate — nothing is stale
    threshold = now - timedelta(seconds=threshold_seconds)
    stale = []
    for s in sessions:
        hb = _parse_iso_utc(getattr(s, "last_heartbeat", "") or getattr(s, "started_at", ""))
        if hb is None:
            continue  # per-session fail-safe
        if hb < threshold:
            stale.append(s)
    return stale


def remove_sessions(sessions, to_remove):
    """Return a new list excluding sessions that are in `to_remove` (identity-based)."""
    to_remove_ids = {id(s) for s in to_remove}
    return [s for s in sessions if id(s) not in to_remove_ids]
