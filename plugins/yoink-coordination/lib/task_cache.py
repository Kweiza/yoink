"""Session-scoped local cache for `task_summary` presence (v0.3.11).

UserPromptSubmit fires on every user turn and, pre-cache, hit the GitHub
API to check whether the current session has a task_summary set. At
1.4~2.1s per prompt this was a significant UX tax. A task_summary, once
set, does not revert during a session — so we persist a stamp file keyed
by (worktree_path, branch) and let the hook short-circuit on existence.

The cache is best-effort: it only suppresses the reminder injection. On
cache miss (first call, or after clear) the hook falls back to the live
API check. Cache clears are not required for correctness — stale stamps
just mean the reminder stays silent longer than strictly necessary.

Key format: sha1("<worktree>::<branch>")[:16].hex
Stamp path: ~/.claude/cache/yoink/task-set/<key>.stamp
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path


_ROOT_ENV = "YOINK_TASK_CACHE_ROOT"  # tests override via env var


def _root() -> Path:
    override = os.environ.get(_ROOT_ENV)
    if override:
        return Path(override)
    return Path.home() / ".claude" / "cache" / "yoink" / "task-set"


def _key(worktree_path: str, branch: str) -> str:
    raw = f"{worktree_path}::{branch}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def stamp_path(worktree_path: str, branch: str) -> Path:
    return _root() / f"{_key(worktree_path, branch)}.stamp"


def is_set(worktree_path: str, branch: str) -> bool:
    try:
        return stamp_path(worktree_path, branch).exists()
    except OSError:
        return False


def mark_set(worktree_path: str, branch: str) -> None:
    p = stamp_path(worktree_path, branch)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    except OSError:
        pass  # fail-silent: cache is best-effort
