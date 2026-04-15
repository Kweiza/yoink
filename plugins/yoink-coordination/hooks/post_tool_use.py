#!/usr/bin/env python3
"""yoink-coordination PostToolUse hook.

v0.3.7+ semantics: release of declared_files happens in the Stop hook when a
path is fully merged into `origin/<primary_branch>`. This hook no longer
releases on commit; it only updates the session's `last_heartbeat` so long
commit sequences keep the session fresh.

Flow:
  1. Parse JSON; only Bash tool relevant.
  2. Run `gitops.is_git_commit_command(command)`; false → exit 0 (no emit).
  3. Check `tool_response.interrupted == False`.
  4. Acquire lock; fetch my issue; update last_heartbeat on matching session;
     write body only when heartbeat changed.
  5. Any error → stderr warning + exit 0.
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


def _bump_heartbeat(ctx, cfg, hook_session_id: Optional[str]) -> None:
    label_status = _label(cfg.label_prefix, constants.LABEL_SUFFIX_STATUS)
    try:
        with lock.acquire(_lock_path(ctx.login, ctx.repo_name_with_owner),
                          timeout=cfg.lock_timeout_seconds):
            issues = github.list_my_status_issues(ctx.login, label_status)
            if not issues:
                return
            issues.sort(key=lambda i: i["number"])
            primary = issues[0]
            num = primary["number"]
            existing = primary.get("body", "")
            parsed, _ = state_mod.parse_body(existing)
            sid = hook_session_id or ctx.claude_session_id
            now = ctx_mod.now_utc_iso()
            changed = False
            for s in parsed.sessions:
                if (
                    (sid and s.claude_session_id == sid)
                    or (s.worktree_path == ctx.worktree_path and s.branch == ctx.branch)
                ):
                    s.last_heartbeat = now
                    changed = True
                    break
            if changed:
                parsed.updated_at = now
                new_body = state_mod.render_body(
                    parsed, login=ctx.login, preserve_tail_from=existing,
                )
                github.edit_issue_body(num, new_body)
    except lock.LockTimeout:
        print("[yoink] lock timeout; PostToolUse heartbeat skipped.", file=sys.stderr)


def run(stdin_text: Optional[str] = None) -> int:
    with telemetry.LatencyTimer("post_tool_use"):
        try:
            raw = stdin_text if stdin_text is not None else sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return 0
        if payload.get("tool_name") != "Bash":
            return 0
        cmd = (payload.get("tool_input") or {}).get("command") or ""
        if not gitops.is_git_commit_command(cmd):
            return 0
        if (payload.get("tool_response") or {}).get("interrupted"):
            return 0
        project_dir = _project_dir()
        if project_dir is None:
            return 0
        if not _gh_auth_ok():
            return 0
        ctx = ctx_mod.build_context()
        if ctx is None:
            return 0
        cfg, _ = cfg_mod.load_config(project_dir)
        hook_session_id = payload.get("session_id")
        try:
            _bump_heartbeat(ctx, cfg, hook_session_id)
        except Exception as e:
            print(f"[yoink] PostToolUse heartbeat failed: {e}", file=sys.stderr)
        return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
