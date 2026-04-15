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


_STATE_SET = "set"          # entry exists with non-empty task_summary
_STATE_EMPTY = "empty"      # entry exists but task_summary is empty/None
_STATE_NO_ENTRY = "none"    # no matching entry yet (session not declared anything)
_STATE_ERROR = "error"      # gh / parsing failure


def _evaluate_task_state(ctx, cfg, hook_session_id: Optional[str]) -> str:
    """Classify the current session's task_summary state. Caller decides
    what to do for each value.

    v0.3.14: split from the prior boolean to fix a self-defeating bug —
    previously the "no matching entry yet" case returned True (skip
    reminder), and the caller then stamped the cache, freezing the
    reminder for the rest of the session even after PreToolUse later
    created an entry with an empty task_summary.
    """
    label_status = _label(cfg.label_prefix, constants.LABEL_SUFFIX_STATUS)
    try:
        issues = github.list_my_status_issues(ctx.login, label_status)
    except Exception:
        return _STATE_ERROR
    if not issues:
        return _STATE_NO_ENTRY
    issues.sort(key=lambda i: i["number"])
    body = issues[0].get("body", "") or ""
    parsed, _ = state_mod.parse_body(body)
    # v0.3.15: single entry per (worktree, branch), shared across sessions
    # that work on the same task. claude_session_id is metadata only.
    matched_session = None
    for s in parsed.sessions:
        if (s.worktree_path == ctx.worktree_path
                and s.branch == ctx.branch):
            matched_session = s
            break
    if matched_session is None:
        return _STATE_NO_ENTRY
    if (matched_session.task_summary or "").strip():
        return _STATE_SET
    return _STATE_EMPTY


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
            state = _evaluate_task_state(ctx, cfg, hook_session_id)
            if state == _STATE_SET:
                task_cache.mark_set(ctx.worktree_path, ctx.branch)
                return 0
            if state in (_STATE_EMPTY, _STATE_NO_ENTRY):
                # v0.3.16: also nag when no entry exists yet. The previous
                # silent-on-no-entry policy made the reminder fire one
                # prompt LATE — by the time the entry was created in
                # PreToolUse, the user's request that triggered the edit
                # had already been processed without Claude seeing the
                # reminder. Now Claude is told from the first prompt to
                # record the task before any file action.
                print(_REMINDER)
                return 0
            # _STATE_ERROR — silent, do NOT mark cache.
        except Exception as e:
            print(f"[yoink] UserPromptSubmit reminder failed: {e}",
                  file=sys.stderr)
        return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
