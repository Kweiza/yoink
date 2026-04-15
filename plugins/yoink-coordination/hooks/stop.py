#!/usr/bin/env python3
"""yoink-coordination Stop hook.

v0.3.7+ semantics: declared_files are released when my changes to a path are
fully merged into `origin/<primary_branch>`. A path is also released when
it's no longer in the working tree AND has no ahead-of-primary commits —
i.e. I reverted my edits entirely.

Concretely, a declared path is KEPT iff:
  - it is in `git status --porcelain` (still dirty / I'm editing), OR
  - `git rev-list origin/<primary>..HEAD -- <path>` is non-empty (committed
    but not yet merged to primary).
Else it is RELEASED: per-path `release` metric emitted with `held_seconds`
and `trigger="merged"` (or `"reverted"` when the working tree is the reason).

Also handles Phase 4 heartbeat cooldown: writes the body even without
structural change when heartbeat is stale enough.
"""
from __future__ import annotations
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Tuple

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import constants            # noqa
import config as cfg_mod    # noqa
import context as ctx_mod   # noqa
import github               # noqa
import state as state_mod   # noqa
import lock                 # noqa
import gitops               # noqa
import telemetry            # noqa
import task_cache           # noqa


def _heartbeat_cooldown_expired(last_heartbeat: str, now_iso: str, cooldown_s: int) -> bool:
    def _p(s):
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
    hb = _p(last_heartbeat)
    n = _p(now_iso)
    if hb is None or n is None:
        return False
    return (n - hb) > timedelta(seconds=cooldown_s)


def _held_seconds(declared_at: str, now_iso: str) -> int:
    """(now - declared_at) in seconds, floored to int. 0 on parse error."""
    def _p(s):
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
    d = _p(declared_at)
    n = _p(now_iso)
    if d is None or n is None:
        return 0
    return max(0, int((n - d).total_seconds()))


def _label(prefix: str, suffix: str) -> str:
    return f"{prefix}:{suffix}"


def _project_dir() -> Optional[Path]:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else None


def _lock_path(login: str, repo: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "__", f"{login}-{repo}")
    return constants.CACHE_DIR / f"{slug}.lock"


def _parse_payload(stdin_text: Optional[str]) -> dict:
    try:
        raw = stdin_text if stdin_text is not None else sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def _release_merged(declared_files: List[dict], project_dir: Path,
                    primary_branch: str, now_iso: str) -> Tuple[List[dict], List[dict]]:
    """Split `declared_files` into (kept, released).

    Kept: path is in dirty working tree OR has unmerged-to-primary commits.
    Released: everything else (fully merged or reverted).

    Emits per-path `release` metrics for each released entry.
    """
    dirty = gitops.working_tree_paths(project_dir)
    if dirty is None:
        # git status failed; fail-open — keep everything to avoid false releases.
        return list(declared_files), []

    kept: List[dict] = []
    released: List[dict] = []
    for entry in declared_files:
        path = entry.get("path", "")
        if not path:
            # Malformed entry — keep as-is; state parsing invariants elsewhere
            # will handle it.
            kept.append(entry)
            continue
        is_dirty = path in dirty
        is_ahead = False
        if not is_dirty:
            # Only check ahead-of-primary when not dirty — small optimization
            # since dirty already means "still editing".
            is_ahead = gitops.path_ahead_of_primary(project_dir, primary_branch, path)
        if is_dirty or is_ahead:
            kept.append(entry)
            continue
        # Released — emit metric.
        trigger = "merged" if not is_dirty else "reverted"  # is_dirty is False here
        # (is_dirty == False and is_ahead == False) → the path is neither in
        # my working tree nor ahead of primary. Two interpretations:
        #  - I reverted my changes (never committed), OR
        #  - My changes are fully merged into primary.
        # We can't distinguish cheaply; default label is "merged" since that's
        # the dominant case by design. Reverted-without-commit is rare.
        released.append(entry)
        telemetry.emit(
            "stop", "release",
            path_hash=telemetry.path_hash(path),
            held_seconds=_held_seconds(entry.get("declared_at", ""), now_iso),
            trigger=trigger,
        )
    return kept, released


def run(stdin_text: Optional[str] = None) -> int:
    with telemetry.LatencyTimer("stop"):
        payload = _parse_payload(stdin_text)
        hook_session_id = payload.get("session_id")

        project_dir = _project_dir()
        if project_dir is None or not github.gh_auth_ok():
            return 0
        ctx = ctx_mod.build_context()
        if ctx is None:
            return 0
        cfg, _ = cfg_mod.load_config(project_dir)
        # Primary branch: config override, else origin/HEAD detection, else "main"
        primary_branch = (
            cfg.primary_branch
            or gitops.detect_primary_branch(project_dir)
            or "main"
        )
        label_status = _label(cfg.label_prefix, constants.LABEL_SUFFIX_STATUS)
        try:
            with lock.acquire(_lock_path(ctx.login, ctx.repo_name_with_owner),
                              timeout=cfg.lock_timeout_seconds):
                issues = github.list_my_status_issues(ctx.login, label_status)
                if not issues:
                    return 0
                issues.sort(key=lambda i: i["number"])
                primary = issues[0]
                num = primary["number"]
                existing = primary.get("body", "")
                parsed, _ = state_mod.parse_body(existing)
                changed = False
                entry_emptied = False
                now = ctx_mod.now_utc_iso()
                # v0.3.15: single entry per (worktree, branch) — no ccs match.
                target_idx = None
                for i, s in enumerate(parsed.sessions):
                    if (s.worktree_path == ctx.worktree_path
                            and s.branch == ctx.branch):
                        target_idx = i
                        break
                if target_idx is not None:
                    s = parsed.sessions[target_idx]
                    kept, released = _release_merged(
                        s.declared_files or [], project_dir, primary_branch, now,
                    )
                    if released:
                        s.declared_files = kept
                        changed = True
                    if changed and not s.declared_files:
                        # Task complete — every declared path landed on
                        # primary (or was reverted). Drop the entry; the
                        # task is over.
                        parsed.sessions.pop(target_idx)
                        entry_emptied = True

                cooldown_expired = False
                if not entry_emptied and target_idx is not None:
                    s = parsed.sessions[target_idx]
                    cooldown_expired = _heartbeat_cooldown_expired(
                        s.last_heartbeat, now, cfg.heartbeat_cooldown_seconds,
                    )
                    if changed or cooldown_expired:
                        s.last_heartbeat = now

                if changed or cooldown_expired:
                    parsed.updated_at = now
                    new_body = state_mod.render_body(
                        parsed, login=ctx.login, preserve_tail_from=existing,
                    )
                    github.edit_issue_body(num, new_body)
                    if entry_emptied:
                        # Stamp belongs to this (worktree, branch) entry —
                        # entry gone, stamp must go too so the next task
                        # at this location starts with a fresh reminder.
                        task_cache.clear(ctx.worktree_path, ctx.branch)
                        if not parsed.sessions:
                            label_active = _label(
                                cfg.label_prefix, constants.LABEL_SUFFIX_ACTIVE,
                            )
                            try:
                                github.remove_label(num, label_active)
                                github.close_issue(num)
                            except Exception:
                                pass
        except lock.LockTimeout:
            return 0
        except Exception as e:
            print(f"[yoink] Stop release failed: {e}", file=sys.stderr)
        return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
