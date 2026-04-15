#!/usr/bin/env python3
"""yoink-coordination Stop hook. See spec §3.3 (adopted Stop hook path).

Runs self-cleanup only, no acquire. Triggered on Claude response end (including
`/exit`). Cost is mitigated by skipping the body edit when no paths changed.
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
import claim                # noqa
import telemetry             # noqa


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


def _lock_path(login: str, repo: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "__", f"{login}-{repo}")
    return constants.CACHE_DIR / f"{slug}.lock"


def _parse_payload(stdin_text: Optional[str]) -> dict:
    try:
        raw = stdin_text if stdin_text is not None else sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def run(stdin_text: Optional[str] = None) -> int:
    with telemetry.LatencyTimer("stop"):
        payload = _parse_payload(stdin_text)
        hook_session_id = payload.get("session_id")  # Task 0 A

        project_dir = _project_dir()
        if project_dir is None or not github.gh_auth_ok():
            return 0
        ctx = ctx_mod.build_context()
        if ctx is None:
            return 0
        cfg, _ = cfg_mod.load_config(project_dir)
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
                dirty = gitops.working_tree_paths(project_dir)
                sid = hook_session_id or ctx.claude_session_id
                for s in parsed.sessions:
                    matches = False
                    if sid and s.claude_session_id == sid:
                        matches = True
                    elif s.worktree_path == ctx.worktree_path and s.branch == ctx.branch:
                        matches = True
                    if not matches:
                        continue
                    kept, removed = claim.self_cleanup(s.declared_files or [], dirty)
                    if removed:
                        s.declared_files = kept
                        changed = True
                    break
                # Phase 4 §4.4: also write on cooldown expiry even without structural change.
                now = ctx_mod.now_utc_iso()
                cooldown_expired = False
                for s in parsed.sessions:
                    if (
                        (sid and s.claude_session_id == sid)
                        or (s.worktree_path == ctx.worktree_path and s.branch == ctx.branch)
                    ):
                        cooldown_expired = _heartbeat_cooldown_expired(
                            s.last_heartbeat, now, cfg.heartbeat_cooldown_seconds,
                        )
                        if changed or cooldown_expired:
                            s.last_heartbeat = now
                        break
                if changed or cooldown_expired:
                    parsed.updated_at = now
                    new_body = state_mod.render_body(
                        parsed, login=ctx.login, preserve_tail_from=existing,
                    )
                    github.edit_issue_body(num, new_body)
        except lock.LockTimeout:
            return 0
        except Exception as e:
            print(f"[yoink] Stop self-cleanup failed: {e}", file=sys.stderr)
        return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
