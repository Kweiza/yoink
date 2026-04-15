"""Runtime context detection for yoink-coordination.
See spec §3.5 and §4.1 steps 1-2."""
from __future__ import annotations
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

TASK_ISSUE_REGEX = re.compile(
    r"(?:^|[/_-])(?:issue|fix|feat|feature|bug|chore|hotfix)[/_-](\d+)(?:[/_-]|$)"
)

@dataclass
class Context:
    login: str
    repo_name_with_owner: str
    branch: str
    worktree_path: str
    task_issue: Optional[str]
    session_id: str
    claude_session_id: Optional[str]
    started_at: str

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def extract_task_issue(branch: str, repo: str) -> Optional[str]:
    m = TASK_ISSUE_REGEX.search(branch)
    if not m:
        return None
    return f"{repo}#{m.group(1)}"

def _run(cmd: list) -> Optional[str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

def detect_login() -> Optional[str]:
    return _run(["gh", "api", "user", "--jq", ".login"])

def detect_repo() -> Optional[str]:
    return _run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])

def detect_branch() -> Optional[str]:
    return _run(["git", "symbolic-ref", "--short", "HEAD"])

def detect_worktree() -> Optional[str]:
    # Prefer CLAUDE_PROJECT_DIR (set by Claude Code, confirmed by Task 0)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return env
    return _run(["git", "rev-parse", "--show-toplevel"])

_CLAUDE_SESSION_UUID_RE = re.compile(r"session-env/([0-9a-f-]{36})/")

def detect_claude_session_id() -> Optional[str]:
    """Claude Code v2.1.105 does not set CLAUDE_SESSION_ID directly. It sets
    CLAUDE_ENV_FILE whose path contains the session UUID (confirmed in Task 0)."""
    env_file = os.environ.get("CLAUDE_ENV_FILE", "")
    m = _CLAUDE_SESSION_UUID_RE.search(env_file)
    return m.group(1) if m else None

def build_context() -> Optional[Context]:
    login = detect_login()
    repo = detect_repo()
    branch = detect_branch()
    worktree = detect_worktree()
    if not all([login, repo, branch, worktree]):
        return None
    return Context(
        login=login, repo_name_with_owner=repo, branch=branch, worktree_path=worktree,
        task_issue=extract_task_issue(branch, repo),
        session_id=str(uuid.uuid4()),
        claude_session_id=detect_claude_session_id(),
        started_at=now_utc_iso(),
    )
