"""Git-side helpers for Phase 3.

Includes:
- `is_git_commit_command(cmd)`                   — §3.2.1 matcher
- `committed_paths_in_head(cwd)`                 — `git show --name-only HEAD` parser (Task 4)
- `working_tree_paths(cwd)`                      — `git status --porcelain` parser (Task 4)
- `is_path_gitignored(cwd, path)`                — `git check-ignore` wrapper (Task 4)

All subprocess calls fail-open (return None / False / empty on error). Raising is
never allowed from this module — callers in hooks rely on graceful degradation.
"""
from __future__ import annotations
import re
import shlex
import subprocess
from pathlib import Path
from typing import List, Optional, Set

# Shell statement separators (simple cases; complex shells like subshells are
# out of scope per spec §3.2.1 "unsupported").
_SEGMENT_SEP_RE = re.compile(r"&&|\|\||;|\n")

# Options on the `git` command that take a following value (e.g., `git -C path commit`).
_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree"}


def _iter_segments(command: str):
    """Split a command line into statement-level segments."""
    for seg in _SEGMENT_SEP_RE.split(command or ""):
        yield seg.strip()


def _tokens_after_git(tokens: List[str]) -> Optional[List[str]]:
    """Given a token list, locate the first bare `git` token and return tokens
    after it with `git`'s value-taking options skipped. Return None if no
    top-level `git` appears."""
    i = 0
    while i < len(tokens):
        if tokens[i] == "git":
            j = i + 1
            while j < len(tokens):
                t = tokens[j]
                # Consume `-C path`, `-c k=v`, `--git-dir x`, etc.
                if t in _GIT_VALUE_OPTS:
                    j += 2
                    continue
                if "=" in t and t.split("=", 1)[0] in _GIT_VALUE_OPTS:
                    j += 1
                    continue
                if t.startswith("-"):  # other `-` options with no value
                    j += 1
                    continue
                break
            return tokens[j:]
        i += 1
    return None


def is_git_commit_command(command: str) -> bool:
    """Return True iff `command`, parsed per spec §3.2.1, contains a `git commit`
    invocation in any of its statement segments.

    Rules:
    - Split on `&&`, `||`, `;`, `\\n`.
    - shlex.split each segment.
    - After the first bare `git` token in a segment, skip value-taking options
      (`-C path`, `-c k=v`, `--git-dir=...`, `--work-tree=...`) and other `-` flags.
    - Next non-option token must be exactly `commit` (regex `^commit$`).
    - `commit-tree`, `commit-graph`, etc. do not match.
    - Heredocs / echo'd strings are filtered automatically by shlex.split.
    - Aliases (`gc`) and `eval "..."` are intentionally NOT supported.
    """
    for segment in _iter_segments(command):
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        rest = _tokens_after_git(tokens)
        if not rest:
            continue
        if rest[0] == "commit":
            return True
    return False


def _run_git(cwd: Path, args: List[str], timeout: int = 5) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def working_tree_paths(cwd: Path) -> Optional[Set[str]]:
    """Return a set of paths that show up in `git status --porcelain=v1 -z`.

    Includes modified, added, deleted, renamed (both old and new names), and untracked.
    Returns None if the repo cannot be queried (e.g., non-repo) — callers
    should treat this as "skip self-cleanup, continue" (fail-open).
    """
    r = _run_git(cwd, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
    if r is None or r.returncode != 0:
        return None
    paths: Set[str] = set()
    # porcelain v1 -z format: XY<space>path\0, rename: XY<space>new\0old\0
    raw = r.stdout
    i = 0
    while i < len(raw):
        end = raw.find("\0", i)
        if end == -1:
            break
        entry = raw[i:end]
        i = end + 1
        if len(entry) < 3:
            continue
        status = entry[:2]
        path = entry[3:]
        paths.add(path)
        # For renames (`R `) and copies (`C `), the next nul-terminated chunk is the old name.
        if status[0] in ("R", "C"):
            end2 = raw.find("\0", i)
            if end2 == -1:
                break
            old = raw[i:end2]
            paths.add(old)
            i = end2 + 1
    return paths


def committed_paths_in_head(cwd: Path) -> Optional[Set[str]]:
    """Return the set of paths touched by HEAD, or None on failure.

    Used by PostToolUse after a detected `git commit`.
    """
    r = _run_git(cwd, ["show", "--name-only", "--pretty=format:", "HEAD"])
    if r is None or r.returncode != 0:
        return None
    return {line for line in r.stdout.splitlines() if line.strip()}


def is_path_gitignored(cwd: Path, path: str) -> bool:
    """Return True iff `git check-ignore` considers `path` gitignored.

    Fail-open: any error → False (proceed with claim). Rationale: worst case
    is a false claim that self-cleanup will clear on the next PreToolUse.
    """
    r = _run_git(cwd, ["check-ignore", "-q", path])
    if r is None:
        return False
    # Exit codes: 0 = ignored, 1 = not ignored, 128 = error (treat as not ignored)
    return r.returncode == 0
