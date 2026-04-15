#!/usr/bin/env python3
"""yoink-coordination UserPromptSubmit hook (v0.3.8+).

Fires on every user message submission. If the current session's
task_summary is not yet recorded, injects a reminder into Claude's
context via stdout — this text is prepended to the user's prompt so
Claude sees it and can invoke `/yoink-coordination:task "<summary>"`.

Once the session has a task_summary, the hook is a no-op (still emits
latency metric for consistency).

Fail-open in all error paths; never blocks user prompts.
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
import telemetry            # noqa: E402
import task_cache           # noqa: E402


def _label(prefix: str, suffix: str) -> str:
    return f"{prefix}:{suffix}"


def _project_dir() -> Optional[Path]:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else None


def _task_set_for_current_session(ctx, cfg, hook_session_id: Optional[str]) -> bool:
    """Return True iff our session entry in the yoink:status issue has a
    non-empty task_summary. Defaults to True on any gh / lookup failure so
    we never spam the reminder during an outage.

    v0.3.13: mirror pre_tool_use._find_my_session matching rules — when
    we know our own session_id we never inherit an entry that has a
    different claude_session_id (those belong to past sessions).
    """
    label_status = _label(cfg.label_prefix, constants.LABEL_SUFFIX_STATUS)
    try:
        issues = github.list_my_status_issues(ctx.login, label_status)
    except Exception:
        return True
    if not issues:
        return True
    issues.sort(key=lambda i: i["number"])
    body = issues[0].get("body", "") or ""
    parsed, _ = state_mod.parse_body(body)
    sid = hook_session_id or ctx.claude_session_id
    matched_session = None
    if sid:
        for s in parsed.sessions:
            if s.claude_session_id == sid:
                matched_session = s
                break
        if matched_session is None:
            for s in parsed.sessions:
                if (not s.claude_session_id
                        and s.worktree_path == ctx.worktree_path
                        and s.branch == ctx.branch):
                    matched_session = s
                    break
    else:
        for s in parsed.sessions:
            if (s.worktree_path == ctx.worktree_path
                    and s.branch == ctx.branch):
                matched_session = s
                break
    if matched_session is None:
        return True  # no matching session yet — session not declared anything
    return bool((matched_session.task_summary or "").strip())


_REMINDER = (
    "[yoink] SYSTEM INSTRUCTION — this session has declared files but no "
    "task_summary recorded.\n"
    "BEFORE replying to the user's request below, you MUST invoke the "
    "slash command as your first action:\n"
    "    /yoink-coordination:task \"<1~2 sentence summary of your goal>\"\n"
    "Teammates see this summary in the yoink:status issue and rely on it "
    "to understand what you're doing in parallel. Skipping this step "
    "breaks coordination."
)


def run(stdin_text: Optional[str] = None) -> int:
    with telemetry.LatencyTimer("user_prompt_submit"):
        try:
            raw = stdin_text if stdin_text is not None else sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return 0
        hook_session_id = payload.get("session_id")
        project_dir = _project_dir()
        if project_dir is None:
            return 0
        # v0.3.11 fast path: if a stamp file for this worktree+branch exists
        # we know task_summary was set (via CLI) — skip the gh round-trip
        # entirely. This trims ~1.5~2s off every user turn in a session
        # that has already recorded its task.
        ctx = ctx_mod.build_context()
        if ctx is None:
            return 0
        if task_cache.is_set(ctx.worktree_path, ctx.branch):
            return 0
        if not github.gh_auth_ok():
            return 0
        cfg, _ = cfg_mod.load_config(project_dir)
        try:
            if _task_set_for_current_session(ctx, cfg, hook_session_id):
                # Live check said task is set — persist to cache so next
                # prompts go through the fast path.
                task_cache.mark_set(ctx.worktree_path, ctx.branch)
                return 0
            # stdout from UserPromptSubmit is prepended to Claude's
            # user-turn context. This makes the reminder persistent
            # until the task is recorded.
            print(_REMINDER)
        except Exception as e:
            print(f"[yoink] UserPromptSubmit reminder failed: {e}",
                  file=sys.stderr)
        return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
