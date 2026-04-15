"""Bootstrap helpers for /yoink-coordination:bootstrap (v0.3.7+).

Currently provides `ensure_config_file(cwd)` which materializes a minimal
`.claude/yoink.config.json` with the repo's primary branch so downstream
merge-based release logic (stop hook) has a target branch to compare against.

Kept as a separate module (rather than inlined in `bin/team-status`) so unit
tests can import it cleanly without wrangling the scriptless CLI.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import constants
import gitops


def ensure_config_file(cwd: Path) -> None:
    """Create `<cwd>/.claude/yoink.config.json` with primary_branch set.

    Does nothing if the file already exists. Primary branch is detected via
    `origin/HEAD`; if detection fails, defaults to "main". Other config keys
    are deliberately omitted — users who truly need to override them can add
    the keys manually, but participants should not see them and be tempted
    to toggle `conflict_mode`.
    """
    target = cwd / constants.CONFIG_FILENAME
    if target.exists():
        print(f"[yoink] config {target}: ok (exists, unchanged)")
        return
    primary = gitops.detect_primary_branch(cwd) or "main"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"primary_branch": primary}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        print(f"[yoink] config {target}: could not write ({e})", file=sys.stderr)
        return
    print(f"[yoink] config {target}: created (primary_branch={primary})")
