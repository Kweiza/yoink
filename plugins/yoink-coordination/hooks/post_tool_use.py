#!/usr/bin/env python3
"""yoink-coordination PostToolUse hook. See spec §3.2 + §3.2.1.

Flow:
  1. Parse JSON; only Bash tool relevant.
  2. Run `gitops.is_git_commit_command(command)`; false → exit 0.
  3. Check `tool_response.interrupted == False` (Task 0 E: no exit_code field
     in v2.1.105; `interrupted` + trusting `git show HEAD` is the replacement).
  4. Fetch HEAD paths via `gitops.committed_paths_in_head`.
  5. Acquire lock; fetch my issue; apply release; write body.
  6. Any error → stderr warning + exit 0.

False-positive safety: if `git commit` failed (e.g., "nothing to commit"),
HEAD is unchanged, so release tries to drop paths already released — no-op
because `claim.release` is idempotent.
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional, Set

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import constants            # noqa
import config as cfg_mod    # noqa
import context as ctx_mod   # noqa
import github               # noqa
import state as state_mod   # noqa
import lock                 # noqa
import gitops               # noqa
import claim                # noqa
import telemetry             # noqa: E402


def _project_dir() -> Optional[Path]:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else None


def _gh_auth_ok() -> bool:
    return github.gh_auth_ok()


def _committed(project_dir: Path) -> Optional[Set[str]]:
    return gitops.committed_paths_in_head(project_dir)


def _label(prefix: str, suffix: str) -> str:
    return f"{prefix}:{suffix}"


def _lock_path(login: str, repo: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "__", f"{login}-{repo}")
    return constants.CACHE_DIR / f"{slug}.lock"


def _apply_release(ctx, cfg, project_dir: Path, hook_session_id: Optional[str],
                   committed: Set[str]) -> None:
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
            changed = False
            sid = hook_session_id or ctx.claude_session_id
            matched = False
            for s in parsed.sessions:
                matches = False
                if sid and s.claude_session_id == sid:
                    matches = True
                elif s.worktree_path == ctx.worktree_path and s.branch == ctx.branch:
                    matches = True
                if not matches:
                    continue
                matched = True
                declared_before = list(s.declared_files or [])
                if not declared_before:
                    telemetry.emit(
                        "post_tool_use", "release_skipped",
                        reason="no_declared",
                    )
                    break
                new_declared, removed = claim.release(
                    declared_before, committed_paths=committed,
                )
                telemetry.emit(
                    "post_tool_use", "release_applied",
                    committed_count=len(committed),
                    declared_before_count=len(declared_before),
                    removed_count=len(removed),
                    matched_session=True,
                )
                if removed:
                    s.declared_files = new_declared
                    changed = True
                break
            if not matched:
                telemetry.emit(
                    "post_tool_use", "release_skipped",
                    reason="no_session",
                )
            if changed:
                now = ctx_mod.now_utc_iso()
                # Phase 4: release is also activity — bump heartbeat.
                for s in parsed.sessions:
                    if (
                        (hook_session_id and s.claude_session_id == hook_session_id)
                        or (s.worktree_path == ctx.worktree_path and s.branch == ctx.branch)
                    ):
                        s.last_heartbeat = now
                        break
                parsed.updated_at = now
                new_body = state_mod.render_body(
                    parsed, login=ctx.login, preserve_tail_from=existing,
                )
                github.edit_issue_body(num, new_body)
    except lock.LockTimeout:
        print("[yoink] lock timeout; PostToolUse release skipped.", file=sys.stderr)


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
        # Task 0 E: no exit_code field; check interrupted instead.
        if (payload.get("tool_response") or {}).get("interrupted"):
            return 0
        project_dir = _project_dir()
        if project_dir is None:
            return 0
        if not _gh_auth_ok():
            return 0
        committed = _committed(project_dir)
        if not committed:
            telemetry.emit("post_tool_use", "release_skipped",
                           reason="committed_empty")
            return 0
        ctx = ctx_mod.build_context()
        if ctx is None:
            return 0
        cfg, _ = cfg_mod.load_config(project_dir)
        hook_session_id = payload.get("session_id")
        try:
            _apply_release(ctx, cfg, project_dir, hook_session_id, committed)
        except Exception as e:
            print(f"[yoink] PostToolUse release failed: {e}", file=sys.stderr)
        return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
