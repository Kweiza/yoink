"""Single source of truth for shared strings (markers, labels, paths, config keys).
Phase 3+ must extend this file rather than inline strings elsewhere.
"""
from pathlib import Path

# Body markers (versioned — see spec §3.3)
STATE_MARKER_BEGIN = "<!-- yoink:state-json-v1:begin"
STATE_MARKER_END = "yoink:state-json-v1:end -->"

# Labels (prefix is config.label_prefix at runtime; these are defaults)
LABEL_SUFFIX_STATUS = "status"
LABEL_SUFFIX_ACTIVE = "active"
LABEL_SUFFIX_STALE = "stale"

# Config file
CONFIG_FILENAME = ".claude/yoink.config.json"

# Lock file
CACHE_DIR = Path.home() / ".cache" / "yoink"

# Issue title format
ISSUE_TITLE_FORMAT = "[yoink-status] {login}"

# GitHub issue body soft limit (see spec §9.2)
BODY_SIZE_LIMIT = 65536

# Config defaults
DEFAULT_CONFLICT_MODE = "advisory"
DEFAULT_LABEL_PREFIX = "yoink"
DEFAULT_LOCK_TIMEOUT_SECONDS = 10
ALLOWED_CONFLICT_MODES = ("advisory", "block")
LABEL_PREFIX_PATTERN = r"^[a-z][a-z0-9_-]*$"

# Phase 3 additions (§3.4, §4.1)
DRIVEN_BY_CLAUDE_CODE = "claude-code"

# Spec §3.1 step 2 — git check-ignore exit codes
GIT_CHECK_IGNORE_IS_IGNORED = 0
GIT_CHECK_IGNORE_NOT_IGNORED = 1

# Phase 4 additions (§3 of phase4 spec)
DEFAULT_HEARTBEAT_COOLDOWN_SECONDS = 120
DEFAULT_STALE_THRESHOLD_SECONDS = 900
