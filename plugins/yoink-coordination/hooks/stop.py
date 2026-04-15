#!/usr/bin/env python3
"""yoink-coordination Stop hook.

v0.3.26: Release detection fully delegated to the GitHub Actions release
workflow (see templates/github/workflows/yoink-release.yml). The client-
side Stop hook no longer inspects merge state — it just bumps the
current session's heartbeat on cooldown expiry so the yoink:status issue
table stays fresh.

Fail-open in every error path.
"""
from __future__ import annotations
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
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
import telemetry            # noqa


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
        hook_session_id = payload.get("session_id")

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

                now = ctx_mod.now_utc_iso()
                sid = hook_session_id or ctx.claude_session_id
                target = None
                if sid:
                    for s in parsed.sessions:
                        if s.claude_session_id == sid:
                            target = s
                            break
                    if target is None:
                        for s in parsed.sessions:
                            if (not s.claude_session_id
                                    and s.worktree_path == ctx.worktree_path
                                    and s.branch == ctx.branch):
                                target = s
                                break
                else:
                    for s in parsed.sessions:
                        if (s.worktree_path == ctx.worktree_path
                                and s.branch == ctx.branch):
                            target = s
                            break

                if target is None:
                    return 0

                if not _heartbeat_cooldown_expired(
                    target.last_heartbeat, now, cfg.heartbeat_cooldown_seconds,
                ):
                    return 0

                target.last_heartbeat = now
                parsed.updated_at = now
                new_body = state_mod.render_body(
                    parsed, login=ctx.login, preserve_tail_from=existing,
                )
                github.edit_issue_body(num, new_body)
        except lock.LockTimeout:
            return 0
        except Exception as e:
            print(f"[yoink] Stop heartbeat failed: {e}", file=sys.stderr)
        return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
