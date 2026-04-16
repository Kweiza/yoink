#!/usr/bin/env python3
"""yoink-coordination Stop hook.

v0.3.28: heartbeat retired. Release detection lives in the GitHub
Actions workflow (v0.3.19~25) and the Stop hook no longer has a
body-write responsibility. Kept as a latency-only emit so the Phase 5
telemetry's M7 (hook overhead) continues to cover "Claude response
end" events and so a future hook body has a ready entrypoint.
"""
from __future__ import annotations
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))

import telemetry  # noqa: E402


def main() -> int:
    with telemetry.LatencyTimer("stop"):
        return 0


if __name__ == "__main__":
    sys.exit(main())
