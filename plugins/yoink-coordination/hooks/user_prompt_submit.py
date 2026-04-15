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


def _label(prefix: str, suffix: str) -> str:
    return f"{prefix}:{suffix}"


def _project_dir() -> Optional[Path]:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else None


def _task_set_for_current_session(ctx, cfg, hook_session_id: Optional[str]) -> bool:
    """Return True iff our session entry in the yoink:status issue has a
    non-empty task_summary. Defaults to True on any gh / lookup failure so
    we never spam the reminder during an outage."""
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
    for s in parsed.sessions:
        matched = False
        if sid and s.claude_session_id == sid:
            matched = True
        elif (s.worktree_path == ctx.worktree_path
              and s.branch == ctx.branch):
            matched = True
        if matched:
            return bool((s.task_summary or "").strip())
    return True  # no matching session yet — session_start hasn't run; skip


_REMINDER = (
    "[yoink] Your current session has no task goal recorded.\n"
    "Before or after handling this request, briefly summarize your goal "
    "(1~2 sentences) by invoking:\n"
    "    /yoink-coordination:task \"<summary>\"\n"
    "Teammates see this summary in the yoink:status issue body."
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
        if project_dir is None or not github.gh_auth_ok():
            return 0
        ctx = ctx_mod.build_context()
        if ctx is None:
            return 0
        cfg, _ = cfg_mod.load_config(project_dir)
        try:
            if not _task_set_for_current_session(ctx, cfg, hook_session_id):
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
