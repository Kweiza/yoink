#!/usr/bin/env python3
"""yoink-coordination SessionStart hook. See spec §4.1.

v0.3.10 lazy-session change: SessionStart no longer creates a yoink:status
issue or registers a session entry. A session entry is only declared when
the session actually modifies a file (handled in PreToolUse). SessionStart
still performs self-heal on my existing entries (if the issue already
exists) and prints peer activity for context.
"""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import constants, github, context as ctx_mod, config as cfg_mod, state as state_mod, lock, render, claim  # noqa: E402
import telemetry  # noqa: E402

def _label(prefix: str, suffix: str) -> str:
    return f"{prefix}:{suffix}"

def _lock_path(login: str, repo: str) -> Path:
    slug = re.sub(r"[^A-Za-z0-9]+", "__", f"{login}-{repo}")
    return constants.CACHE_DIR / f"{slug}.lock"


def _print_other_members(ctx, cfg):
    label = _label(cfg.label_prefix, constants.LABEL_SUFFIX_STATUS)
    others = github.list_other_status_issues_open(ctx.login, label)
    if not others:
        print("[yoink] no other team members currently active in this repo.")
        return
    members = []
    for iss in others:
        assignees = iss.get("assignees") or []
        if not assignees:
            continue
        login = assignees[0]["login"]
        parsed, corrupt = state_mod.parse_body(iss.get("body", ""))
        members.append({"login": login, "state": None if corrupt else parsed,
                        "issue_number": iss["number"]})
    print("[yoink] other active members:")
    print(render.team_status_markdown(
        members,
        stale_threshold_seconds=cfg.stale_threshold_seconds,
        now_iso=ctx_mod.now_utc_iso(),
    ))

def main() -> int:
    with telemetry.LatencyTimer("session_start"):
        if not github.gh_auth_ok():
            print("[yoink] gh auth missing; hook skipped.", file=sys.stderr)
            return 0
        ctx = ctx_mod.build_context()
        if ctx is None:
            print("[yoink] could not detect context; hook skipped.", file=sys.stderr)
            return 0
        cfg, warnings = cfg_mod.load_config(Path(ctx.worktree_path))
        for w in warnings:
            print(f"[yoink] {w}", file=sys.stderr)

        label_status = _label(cfg.label_prefix, constants.LABEL_SUFFIX_STATUS)

        if not github.label_exists(label_status):
            print(f"[yoink] label '{label_status}' not present in this repo; skipping. "
                  f"Run `/yoink-coordination:bootstrap` to opt in.", file=sys.stderr)
            return 0

        # v0.3.10: Do not create an issue here. Only self-heal if one exists.
        try:
            lockfile = _lock_path(ctx.login, ctx.repo_name_with_owner)
            with lock.acquire(lockfile, timeout=cfg.lock_timeout_seconds):
                issues = github.list_my_status_issues(ctx.login, label_status)
                if issues:
                    issues.sort(key=lambda i: i["number"])
                    primary = issues[0]
                    num = primary["number"]
                    extras = [i["number"] for i in issues[1:]]
                    if extras:
                        print(f"[yoink] multiple status issues found; using #{num}. "
                              f"Duplicates: {', '.join(f'#{n}' for n in extras)}. "
                              f"Please close or merge them manually.", file=sys.stderr)
                    existing_body = primary.get("body", "")
                    parsed, corrupt = state_mod.parse_body(existing_body)
                    if corrupt:
                        print(f"[yoink] body of issue #{num} was unparseable; leaving untouched.", file=sys.stderr)
                    else:
                        stale = claim.find_stale_sessions(
                            parsed.sessions,
                            now_iso=ctx_mod.now_utc_iso(),
                            threshold_seconds=cfg.stale_threshold_seconds,
                        )
                        if stale:
                            parsed.sessions = claim.remove_sessions(parsed.sessions, stale)
                            parsed.updated_at = ctx_mod.now_utc_iso()
                            print(f"[yoink] self-heal: removed {len(stale)} stale session(s)", file=sys.stderr)
                            telemetry.emit("session_start", "self_heal", stale_removed=len(stale))
                            new_body = state_mod.render_body(parsed, login=ctx.login, preserve_tail_from=existing_body)
                            if state_mod.body_exceeds_limit(new_body):
                                print("[yoink] warning: issue body exceeds 65536-char limit.", file=sys.stderr)
                            github.edit_issue_body(num, new_body)
        except lock.LockTimeout:
            print("[yoink] lock timeout; skipping self-heal for this session start.", file=sys.stderr)
            return 0

        _print_other_members(ctx, cfg)
        return 0

if __name__ == "__main__":
    sys.exit(main())
