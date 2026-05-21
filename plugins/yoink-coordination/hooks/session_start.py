#!/usr/bin/env python3
"""yoink-coordination SessionStart hook.

v0.3.15: under the "task lives until primary-merge" rule, SessionStart
never creates issues or writes the active label.

v0.3.29: on session start we DO sweep my own past session entries
whose remote branch no longer exists (sign that the work was abandoned
or the branch was manually deleted outside Claude). File-level cleanup
stays in PostToolUse (Task 4) and the Actions release workflow.

This hook:
  - emits latency metric
  - prints peer activity for orientation
  - cleans my own stale branch entries
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import constants, github, context as ctx_mod, config as cfg_mod, state as state_mod, render  # noqa: E402
import gitops  # noqa: E402
import lock    # noqa: E402
import telemetry  # noqa: E402

_PRIMARY_BRANCHES = {"main", "master"}


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
        now_iso=ctx_mod.now_utc_iso(),
    ))


def _clean_my_stale_entries(ctx, cfg) -> None:
    """Remove my session entries whose remote branch has been deleted.

    Only checks branch existence (lightweight). File-level cleanup is
    handled by PostToolUse after actual git revert commands, and by the
    GitHub Actions release workflow on merge-to-primary.
    """
    label_status = _label(cfg.label_prefix, constants.LABEL_SUFFIX_STATUS)
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

            cwd = Path(ctx.worktree_path)
            if not gitops.git_repo_healthy(cwd):
                # Don't try to sweep if we can't even reach git — fail-open.
                return
            changed = False
            surviving = []
            for s in parsed.sessions:
                if s.branch in _PRIMARY_BRANCHES:
                    surviving.append(s)
                    continue
                if not s.declared_files:
                    surviving.append(s)
                    continue
                # Prefer the session's own worktree when still reachable — other
                # worktrees on the same repo clone share remotes, but if the
                # session was in a different clone entirely, current cwd's remote
                # may give the wrong answer. `cwd` fallback keeps the common case
                # cheap.
                session_cwd = Path(s.worktree_path) if s.worktree_path else cwd
                if not session_cwd.exists():
                    session_cwd = cwd
                if gitops.remote_branch_exists(session_cwd, s.branch):
                    surviving.append(s)
                    continue
                changed = True
                telemetry.emit(
                    "session_start", "stale_branch_cleanup",
                    branch_hash=telemetry.path_hash(s.branch),
                )

            if not changed:
                return
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
        print("[yoink] lock timeout; SessionStart cleanup skipped.",
              file=sys.stderr)
    except Exception as e:
        print(f"[yoink] SessionStart cleanup failed: {e}", file=sys.stderr)


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

        _print_other_members(ctx, cfg)
        _clean_my_stale_entries(ctx, cfg)
        return 0


if __name__ == "__main__":
    sys.exit(main())
