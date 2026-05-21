#!/usr/bin/env python3
"""yoink-coordination PostToolUse hook.

v0.3.29: git commit is a pure noop (heartbeat retired in v0.3.28, release
handled by the GitHub Actions workflow since v0.3.19~25). This hook now
detects git revert-like commands (checkout, restore, reset --hard, switch)
and releases declared_files in the CURRENT session whose state reverted
to base — working-tree clear AND not in branch diff vs main.

Scoped to the current session (matched by claude_session_id, with legacy
(worktree, branch) fallback) to avoid cross-session interference when the
same user has multiple Claude sessions on different worktrees.

Flow:
  1. Parse JSON; only Bash tool relevant.
  2. If tool_response.interrupted → exit 0.
  3. If neither is_git_commit_command nor is_git_revert_command → exit 0.
  4. For revert: acquire lock, locate current session, fetch git state
     ONCE (working_tree_paths + branch_diff_paths), sweep that session's
     declared_files, write body if changed, close issue if all sessions
     drop.
  5. Any error → stderr warning + exit 0 (fail-open).
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

import constants            # noqa
import config as cfg_mod    # noqa
import context as ctx_mod   # noqa
import github               # noqa
import state as state_mod   # noqa
import lock                 # noqa
import gitops               # noqa
import telemetry            # noqa: E402


def _project_dir() -> Optional[Path]:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else None


def _gh_auth_ok() -> bool:
    return github.gh_auth_ok()


def _label(prefix: str, suffix: str) -> str:
    return f"{prefix}:{suffix}"


def _lock_path(login: str, repo: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "__", f"{login}-{repo}")
    return constants.CACHE_DIR / f"{slug}.lock"


def _find_my_session(parsed_state, hook_session_id, ctx):
    """Match current session in the parsed state. Same pattern as
    pre_tool_use.py::_find_my_session (v0.3.18 strict-by-ccs with
    legacy (worktree, branch) fallback for pre-ccs entries)."""
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
    for s in parsed_state.sessions:
        if s.worktree_path == ctx.worktree_path and s.branch == ctx.branch:
            return s
    return None


def _self_cleanup(ctx, cfg, hook_session_id: Optional[str]) -> None:
    """Release declared_files in MY CURRENT SESSION that reverted to base
    after a git revert command. Scoped to the current session to avoid
    cross-session interference (e.g., other worktrees sharing my login).
    """
    label_status = _label(cfg.label_prefix, constants.LABEL_SUFFIX_STATUS)
    cwd = _project_dir()
    if cwd is None:
        return

    try:
        with lock.acquire(_lock_path(ctx.login, ctx.repo_name_with_owner),
                          timeout=cfg.lock_timeout_seconds):
            issues = github.list_my_status_issues(ctx.login, label_status)
            if not issues:
                return
            issues.sort(key=lambda i: i["number"])
            primary = issues[0]
            if (primary.get("state") or "").upper() == "CLOSED":
                return
            num = primary["number"]
            existing = primary.get("body", "")
            parsed, corrupt = state_mod.parse_body(existing)
            if corrupt or not parsed.sessions:
                return

            me = _find_my_session(parsed, hook_session_id, ctx)
            if me is None or not me.declared_files:
                return

            wt = gitops.working_tree_paths(cwd)
            bd = gitops.branch_diff_paths(cwd, "main")
            if wt is None or bd is None:
                return

            kept = []
            changed = False
            for entry in me.declared_files:
                if not isinstance(entry, dict):
                    # Forward-compat: preserve unknown entry shapes verbatim.
                    kept.append(entry)
                    continue
                path = entry.get("path", "")
                if not path or path in wt or path in bd:
                    kept.append(entry)
                else:
                    changed = True
                    telemetry.emit(
                        "post_tool_use", "stale_release",
                        path_hash=telemetry.path_hash(path),
                    )
            if not changed:
                return
            me.declared_files = kept

            surviving = [s for s in parsed.sessions
                         if s is not me or s.declared_files]
            if len(surviving) != len(parsed.sessions):
                parsed.sessions = surviving
            parsed.updated_at = ctx_mod.now_utc_iso()
            new_body = state_mod.render_body(
                parsed, login=ctx.login, preserve_tail_from=existing,
            )
            github.edit_issue_body(num, new_body)
            if not parsed.sessions:
                label_active = _label(cfg.label_prefix,
                                      constants.LABEL_SUFFIX_ACTIVE)
                github.remove_label(num, label_active)
                github.close_issue(num)
    except lock.LockTimeout:
        print("[yoink] lock timeout; PostToolUse cleanup skipped.",
              file=sys.stderr)


def run(stdin_text: Optional[str] = None) -> int:
    with telemetry.LatencyTimer("post_tool_use"):
        try:
            raw = stdin_text if stdin_text is not None else sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return 0
        if payload.get("tool_name") != "Bash":
            return 0
        if (payload.get("tool_response") or {}).get("interrupted"):
            return 0
        cmd = (payload.get("tool_input") or {}).get("command") or ""

        is_commit = gitops.is_git_commit_command(cmd)
        is_revert = gitops.is_git_revert_command(cmd)
        if not is_commit and not is_revert:
            return 0
        # is_commit is a pure noop in v0.3.29 (latency emit only).
        if not is_revert:
            return 0

        if _project_dir() is None:
            return 0
        if not _gh_auth_ok():
            return 0
        ctx = ctx_mod.build_context()
        if ctx is None:
            return 0
        cfg, _ = cfg_mod.load_config(_project_dir())
        hook_session_id = payload.get("session_id")
        try:
            _self_cleanup(ctx, cfg, hook_session_id)
        except Exception as e:
            print(f"[yoink] PostToolUse cleanup failed: {e}", file=sys.stderr)
        return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
