#!/usr/bin/env python3
"""yoink-coordination SessionEnd hook. See spec §4.2."""
from __future__ import annotations
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import constants, github, context as ctx_mod, config as cfg_mod, state as state_mod, lock  # noqa: E402
import telemetry  # noqa: E402
from datetime import datetime, timezone


def _held_seconds(declared_at: str, now_iso: str) -> int:
    """(now - declared_at) in seconds, floored to int. 0 on parse error."""
    def _p(s: str):
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
    d = _p(declared_at)
    n = _p(now_iso)
    if d is None or n is None:
        return 0
    return max(0, int((n - d).total_seconds()))

def _session_matches_ending_ctx(s, ctx) -> bool:
    # Match by ccs when both ctx and session have it (e.g., future Claude Code may set
    # CLAUDE_ENV_FILE on SessionEnd; as of v2.1.105 it does not — see journal 12 addendum).
    if ctx.claude_session_id and s.claude_session_id:
        return s.claude_session_id == ctx.claude_session_id
    # Fallback: worktree + branch (SessionEnd always has these via CLAUDE_PROJECT_DIR/git).
    # Only reached when ctx or session lacks a ccs (e.g., v2.1.105 SessionEnd has no ccs).
    return s.worktree_path == ctx.worktree_path and s.branch == ctx.branch

def _label(prefix: str, suffix: str) -> str:
    return f"{prefix}:{suffix}"

def _lock_path(login: str, repo: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "__", f"{login}-{repo}")
    return constants.CACHE_DIR / f"{slug}.lock"

def main() -> int:
    with telemetry.LatencyTimer("session_end"):
        if not github.gh_auth_ok():
            return 0
        ctx = ctx_mod.build_context()
        if ctx is None:
            return 0
        cfg, warnings = cfg_mod.load_config(Path(ctx.worktree_path))
        for w in warnings:
            print(f"[yoink] {w}", file=sys.stderr)

        label_status = _label(cfg.label_prefix, constants.LABEL_SUFFIX_STATUS)
        label_active = _label(cfg.label_prefix, constants.LABEL_SUFFIX_ACTIVE)
        if not github.label_exists(label_status):
            return 0

        try:
            with lock.acquire(_lock_path(ctx.login, ctx.repo_name_with_owner), timeout=cfg.lock_timeout_seconds):
                issues = github.list_my_status_issues(ctx.login, label_status)
                if not issues:
                    return 0
                issues.sort(key=lambda i: i["number"])
                primary = issues[0]
                num = primary["number"]
                existing_body = primary.get("body", "")
                parsed, _ = state_mod.parse_body(existing_body)

                now = ctx_mod.now_utc_iso()
                # v0.3.7+: emit a `release` metric per still-declared path
                # before we drop the ending session. trigger="session_end"
                # distinguishes from merge-based releases in stop.py.
                for s in parsed.sessions:
                    if not _session_matches_ending_ctx(s, ctx):
                        continue
                    for entry in (s.declared_files or []):
                        path = entry.get("path")
                        if not path:
                            continue
                        declared_at = entry.get("declared_at", "") or ""
                        held = _held_seconds(declared_at, now)
                        telemetry.emit(
                            "session_end", "release",
                            path_hash=telemetry.path_hash(path),
                            held_seconds=held,
                            trigger="session_end",
                        )

                parsed.sessions = [s for s in parsed.sessions if not _session_matches_ending_ctx(s, ctx)]
                parsed.updated_at = now

                if not parsed.sessions:
                    new_body = state_mod.render_body(parsed, login=ctx.login, preserve_tail_from=existing_body)
                    github.edit_issue_body(num, new_body)
                    github.remove_label(num, label_active)
                    github.close_issue(num)
                else:
                    new_body = state_mod.render_body(parsed, login=ctx.login, preserve_tail_from=existing_body)
                    github.edit_issue_body(num, new_body)
        except lock.LockTimeout:
            print("[yoink] lock timeout during session end.", file=sys.stderr)
            return 0
        return 0

if __name__ == "__main__":
    sys.exit(main())
