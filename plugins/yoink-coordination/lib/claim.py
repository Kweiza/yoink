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


# v0.3.15 removed the self-heal call site; v0.3.28 removes the unused
# helpers (find_stale_sessions / remove_sessions) along with the
# heartbeat machinery. Session staleness is now implicit: an entry
# survives until the Actions release workflow releases its declared
# paths (or a human deletes the row in the issue body).
