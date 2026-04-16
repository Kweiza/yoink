#!/usr/bin/env python3
"""yoink-coordination SessionStart hook.

v0.3.15: under the "task lives until primary-merge" rule, SessionStart
no longer touches my issue body. Heartbeat-based stale eviction would
remove entries with unmerged declared_files — that contradicts the
single rule, so it's gone. Stamp clearing also removed (the stamp
follows task_summary lifetime, not session lifetime).

This hook only:
  - emits latency metric
  - prints peer activity for orientation
"""
from __future__ import annotations
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import constants, github, context as ctx_mod, config as cfg_mod, state as state_mod, render  # noqa: E402
import telemetry  # noqa: E402


def _label(prefix: str, suffix: str) -> str:
    return f"{prefix}:{suffix}"


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
        return 0


if __name__ == "__main__":
    sys.exit(main())
