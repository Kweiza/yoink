#!/usr/bin/env python3
"""yoink-coordination SessionEnd hook.

v0.3.15: under the "task lives until primary-merge" rule, a session
ending does NOT release any declared paths. The task entry persists,
its claude_session_id stays as last-writer metadata, and any later
session on the same (worktree, branch) inherits the entry. Releases
happen exclusively in stop.py via merge-to-primary detection.

This hook now only emits the latency metric so that user_prompt_submit /
session lifecycle telemetry stays consistent.
"""
from __future__ import annotations
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import telemetry  # noqa: E402


def main() -> int:
    with telemetry.LatencyTimer("session_end"):
        return 0


if __name__ == "__main__":
    sys.exit(main())
