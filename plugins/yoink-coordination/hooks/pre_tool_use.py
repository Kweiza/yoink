#!/usr/bin/env python3
"""yoink-coordination PreToolUse hook. See spec §3.1.

Flow (single lock, single body edit):
  1. Parse hook JSON from stdin. Skip if tool_name not in {Edit, Write}.
  2. Extract & normalize file_path to CLAUDE_PROJECT_DIR-relative POSIX.
  3. If gitignored (`git check-ignore -q`) → passthrough exit 0.
  4. gh auth check (fail-open).
  5. Acquire lock (fail-open on timeout).
  6. Fetch my issue (1 round-trip); bail out fail-open on failure.
  7. Self-cleanup calculation (in-memory) using `git status --porcelain`.
  8. Fetch others via cache.fetch_others → build {path → [owner_dict]}.
  9. Conflict decision via policy.decide.
 10. If block-decision: write self-cleanup-only body edit; print warning; exit blocking.
 11. Otherwise acquire in-memory; write body edit merging cleanup + acquire.
 12. Any error → stderr warning + exit 0 (fail-open).
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import constants            # noqa: E402
import config as cfg_mod    # noqa: E402
import context as ctx_mod   # noqa: E402
import github               # noqa: E402
import state as state_mod   # noqa: E402
import lock                 # noqa: E402
import cache                # noqa: E402
import gitops               # noqa: E402
import policy               # noqa: E402
import claim                # noqa: E402
import warning              # noqa: E402
import telemetry            # noqa: E402

TARGET_TOOLS = {"Edit", "Write"}
BLOCK_EXIT_CODE = 2  # Per spec §5.2; verified in E2E T16.


def _heartbeat_cooldown_expired(last_heartbeat: str, now_iso: str, cooldown_s: int) -> bool:
    """Return True iff (now - last_heartbeat) > cooldown. Fail-safe: False on parse error."""
    from datetime import datetime, timedelta, timezone
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


def _label(prefix: str, suffix: str) -> str:
    return f"{prefix}:{suffix}"


def _project_dir() -> Optional[Path]:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else None


def _normalize_path(project_dir: Path, raw: str) -> Optional[str]:
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = (project_dir / p).resolve()
    try:
        rel = p.relative_to(project_dir.resolve())
    except ValueError:
        return None
    posix = rel.as_posix()
    if posix.startswith("./"):
        posix = posix[2:]
    return posix


def _is_gitignored(project_dir: Path, path: str) -> bool:
    return gitops.is_path_gitignored(project_dir, path)


def _gh_auth_ok() -> bool:
    return github.gh_auth_ok()


def _load_config(project_dir: Path):
    cfg, warnings = cfg_mod.load_config(project_dir)
    for w in warnings:
        print(f"[yoink] {w}", file=sys.stderr)
    return cfg


def _lock_path(login: str, repo: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "__", f"{login}-{repo}")
    return constants.CACHE_DIR / f"{slug}.lock"


def _acquire_lock_ctx(path: Path, timeout: int):
    return lock.acquire(path, timeout=timeout)


def _fetch_my_issue(login: str, label_status: str):
    """Return (issue_number, parsed_state, existing_body) or (None, State, '')."""
    issues = github.list_my_status_issues(login, label_status)
    if not issues:
        return None, state_mod.State(updated_at=""), ""
    issues.sort(key=lambda i: i["number"])
    primary = issues[0]
    body = primary.get("body", "")
    parsed, _corrupt = state_mod.parse_body(body)
    return primary["number"], parsed, body


def _fetch_others(login: str, label_status: str):
    """Return list of {path, owners:[{login,branch,declared_at,task_issue}]}."""
    def fetcher(l, lab):
        return github.list_other_status_issues_open(l, lab)
    others = cache.fetch_others(login, label_status, fetcher)
    index = {}
    for iss in others:
        assignees = iss.get("assignees") or []
        if not assignees:
            continue
        peer_login = assignees[0]["login"]
        parsed, corrupt = state_mod.parse_body(iss.get("body", ""))
        if corrupt:
            continue
        for s in parsed.sessions:
            for entry in getattr(s, "declared_files", []) or []:
                p = (entry or {}).get("path")
                if not isinstance(p, str):
                    continue
                index.setdefault(p, []).append({
                    "login": peer_login,
                    "branch": s.branch,
                    "declared_at": (entry or {}).get("declared_at", ""),
                    "task_issue": s.task_issue,
                })
    return [{"path": p, "owners": o} for p, o in index.items()]


def _write_body(issue_num: int, login: str, state, existing_body: str) -> bool:
    state.updated_at = ctx_mod.now_utc_iso()
    new_body = state_mod.render_body(state, login=login, preserve_tail_from=existing_body)
    if state_mod.body_exceeds_limit(new_body):
        print("[yoink] body exceeds 65536; edit may fail.", file=sys.stderr)
    return github.edit_issue_body(issue_num, new_body)


def _find_my_session(parsed_state, hook_session_id, ctx):
    """v0.3.18: each Claude session has its own entry (matched by
    claude_session_id). Past sessions on the same (worktree, branch)
    keep their entries until their own declared_files merge to primary
    (stop.py releases). A new session NEVER inherits another session's
    entry — it creates its own.

    Legacy entries (no claude_session_id, e.g. pre-v0.3.10 data) fall
    back to (worktree, branch) match for one-off compatibility.
    """
    sid = hook_session_id or ctx.claude_session_id
    if sid:
        for s in parsed_state.sessions:
            if s.claude_session_id == sid:
                return s
        for s in parsed_state.sessions:
            if (not s.claude_session_id
                    and s.worktree_path == ctx.worktree_path
                    and s.branch == ctx.branch):
                return s
        return None
    # No session_id at all (legacy CC) — pure (worktree, branch) fallback.
    for s in parsed_state.sessions:
        if s.worktree_path == ctx.worktree_path and s.branch == ctx.branch:
            return s
    return None


def run(stdin_text: Optional[str] = None) -> int:
    with telemetry.LatencyTimer("pre_tool_use"):
        # 1. Parse input
        try:
            raw = stdin_text if stdin_text is not None else sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return 0  # non-JSON stdin → fail-open
        tool = payload.get("tool_name")
        if tool not in TARGET_TOOLS:
            return 0
        hook_session_id = payload.get("session_id")  # Task 0 A

        # 2. Normalize path
        project_dir = _project_dir()
        if project_dir is None:
            print("[yoink] CLAUDE_PROJECT_DIR not set; skipping.", file=sys.stderr)
            return 0
        raw_path = (payload.get("tool_input") or {}).get("file_path") or ""
        norm = _normalize_path(project_dir, raw_path)
        if not norm:
            print(f"[yoink] could not normalize file_path={raw_path!r}; skipping.", file=sys.stderr)
            return 0

        # 3. Gitignored passthrough
        try:
            if _is_gitignored(project_dir, norm):
                return 0
        except Exception as e:
            print(f"[yoink] check-ignore failed: {e}", file=sys.stderr)

        # 4. gh auth
        if not _gh_auth_ok():
            print("[yoink] gh auth missing; hook skipped.", file=sys.stderr)
            return 0

        ctx = ctx_mod.build_context()
        if ctx is None:
            print("[yoink] context unavailable; skipping.", file=sys.stderr)
            return 0
        cfg = _load_config(project_dir)
        label_status = _label(cfg.label_prefix, constants.LABEL_SUFFIX_STATUS)

        label_active = _label(cfg.label_prefix, constants.LABEL_SUFFIX_ACTIVE)

        # 5. Lock
        try:
            with _acquire_lock_ctx(_lock_path(ctx.login, ctx.repo_name_with_owner),
                                   timeout=cfg.lock_timeout_seconds):
                # 6. Fetch my issue (create lazily on first declare — v0.3.10)
                num, parsed, existing = _fetch_my_issue(ctx.login, label_status)
                issue_created = False
                if num is None:
                    num = github.create_status_issue(ctx.login, label_status)
                    if num is None:
                        print("[yoink] failed to create status issue; skipping acquire.", file=sys.stderr)
                        return 0
                    parsed = state_mod.State(updated_at="")
                    existing = ""
                    issue_created = True
                    telemetry.emit("pre_tool_use", "issue_create")
                me = _find_my_session(parsed, hook_session_id, ctx)
                reconciled = False
                cur_ccs = hook_session_id or ctx.claude_session_id
                if me is None:
                    # First file declare for this (worktree, branch). Lazy-create
                    # the entry. v0.3.15: this is the only entry for this
                    # location; future sessions inherit it until all paths
                    # land on primary (stop.py releases them).
                    me = state_mod.Session(
                        session_id=ctx.session_id,
                        worktree_path=ctx.worktree_path,
                        branch=ctx.branch,
                        task_issue=ctx.task_issue,
                        started_at=ctx.started_at,
                        last_heartbeat=ctx.started_at,
                        declared_files=[],
                        driven_by=constants.DRIVEN_BY_CLAUDE_CODE,
                        claude_session_id=cur_ccs,
                    )
                    parsed.sessions.append(me)
                    reconciled = True
                elif cur_ccs and me.claude_session_id != cur_ccs:
                    # New session inherited the entry — record current
                    # session as last writer for visibility.
                    me.claude_session_id = cur_ccs

                # v0.3.15: self_cleanup removed. Releases happen ONLY in
                # stop.py via merge-to-primary detection. A path stays
                # declared until it's actually on the primary branch.
                new_declared = list(me.declared_files or [])
                removed = []

                # 8. Fetch others
                others_index = _fetch_others(ctx.login, label_status)
                conflicting_owners = []
                for entry in others_index:
                    if entry["path"] == norm:
                        conflicting_owners = entry["owners"]
                        break

                # 9. Decide
                decision = policy.decide(cfg.conflict_mode, conflicting_owners)

                # 10. Block branch
                if decision.should_block:
                    me.declared_files = new_declared  # still apply cleanup
                    # Phase 4: blocked tool call is still user activity — bump heartbeat.
                    me.last_heartbeat = ctx_mod.now_utc_iso()
                    _write_body(num, ctx.login, parsed, existing)
                    msg = warning.format_conflict(
                        path=norm, owners=conflicting_owners,
                        mode=cfg.conflict_mode, now_iso=ctx_mod.now_utc_iso(),
                    )
                    telemetry.emit("pre_tool_use", "conflict",
                                   path_hash=telemetry.path_hash(norm))
                    print(msg, file=sys.stderr)
                    return BLOCK_EXIT_CODE

                # 11. Acquire + single body edit
                now = ctx_mod.now_utc_iso()
                new_declared, changed = claim.acquire(new_declared, norm, now=now)
                me.declared_files = new_declared
                if changed:
                    # New path added to declared_files — emit lifecycle event.
                    # Idempotent: if path was already there, claim.acquire
                    # returns changed=False and we skip.
                    telemetry.emit(
                        "pre_tool_use", "acquire",
                        path_hash=telemetry.path_hash(norm),
                    )
                cooldown_expired = _heartbeat_cooldown_expired(
                    me.last_heartbeat, now, cfg.heartbeat_cooldown_seconds,
                )
                if changed or removed or cooldown_expired or reconciled or issue_created:
                    me.last_heartbeat = now
                    _write_body(num, ctx.login, parsed, existing)
                if issue_created:
                    # Only attach yoink:active when this hook actually created
                    # the issue — avoids a redundant label call on every edit.
                    github.add_label(num, label_active)
                # v0.3.14: PreToolUse stderr at exit 0 is debug-log only —
                # neither Claude nor the user sees it (verified against
                # Claude Code hooks docs). Removed earlier nudges; rely on
                # UserPromptSubmit stdout (the only Claude-visible
                # exit-0 channel for advisory text). Scope-shift detection
                # via PreToolUse would require JSON output or exit-2
                # blocking — deferred to a future patch with explicit
                # design.
                if conflicting_owners:
                    msg = warning.format_conflict(
                        path=norm, owners=conflicting_owners,
                        mode=cfg.conflict_mode, now_iso=ctx_mod.now_utc_iso(),
                    )
                    telemetry.emit("pre_tool_use", "conflict",
                                   path_hash=telemetry.path_hash(norm))
                    print(msg, file=sys.stderr)
        except lock.LockTimeout:
            print("[yoink] lock timeout; skipping PreToolUse acquire.", file=sys.stderr)
            return 0
        except Exception as e:
            print(f"[yoink] PreToolUse failed fail-open: {e}", file=sys.stderr)
            return 0
        return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
