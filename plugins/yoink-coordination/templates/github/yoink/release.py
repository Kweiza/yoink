#!/usr/bin/env python3
"""yoink release script — runs in a GitHub Action on push to default branch.

For each open `yoink:status` issue in this repo:
  1. Parse the body to extract sessions + their declared_files.
  2. Drop any declared path whose merge to the default branch is in this push.
  3. If a session's declared_files becomes empty, drop the session.
  4. If the issue's session list becomes empty, close the issue and remove
     the active label.
  5. Otherwise edit the issue body in place.

Inputs (env):
  GH_TOKEN  — GitHub token with `issues: write`
  REPO      — owner/name of this repo
  BEFORE    — push event "before" SHA (may be 40 zeros for first push)
  AFTER     — push event "after" SHA (current ref tip)
  PRIMARY   — default branch name (informational; release is per-merged-path)

Exits non-zero on any unrecoverable error so the Action surfaces it.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(THIS_DIR))

import state as state_mod  # noqa: E402

LABEL_STATUS = "yoink:status"
LABEL_ACTIVE = "yoink:active"


# In-process cache for the two heuristics below.
_PRIMARY_HIT_CACHE = {}
_BRANCH_READY_CACHE = {}
_SYNCED_CACHE = {}


def _configure_gh_host():
    """Point `gh` CLI at the same GitHub server the Action runs on and
    forward the Action token in the form `gh` expects per host.

    Two tweaks for GitHub Enterprise Server (e.g. GITHUB_SERVER_URL =
    https://github.ecodesamsung.com):
      1. Strip the scheme from GITHUB_SERVER_URL and export GH_HOST so
         `gh` talks to the enterprise server instead of github.com.
      2. `gh` reads enterprise tokens from GH_ENTERPRISE_TOKEN (not
         GH_TOKEN — that one is github.com-only). Workflows typically
         pass the GITHUB_TOKEN through GH_TOKEN, so mirror it into
         GH_ENTERPRISE_TOKEN when we're targeting a non-default host.
    """
    host = os.environ.get("GH_HOST", "").strip()
    if not host:
        server = os.environ.get("GITHUB_SERVER_URL", "").strip()
        if server:
            h = server
            for prefix in ("https://", "http://"):
                if h.startswith(prefix):
                    h = h[len(prefix):]
            h = h.rstrip("/")
            if h and h != "github.com":
                os.environ["GH_HOST"] = h
                host = h

    if host and host != "github.com":
        token = (os.environ.get("GH_ENTERPRISE_TOKEN")
                 or os.environ.get("GITHUB_ENTERPRISE_TOKEN")
                 or os.environ.get("GH_TOKEN")
                 or os.environ.get("GITHUB_TOKEN"))
        if token:
            os.environ["GH_ENTERPRISE_TOKEN"] = token


_configure_gh_host()


def _run(cmd, check=True, capture=True):
    p = subprocess.run(
        cmd, check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    return p.stdout if capture else ""


def _changed_paths(before: str, after: str):
    """All paths touched between BEFORE..AFTER. For first-push (BEFORE=0..0)
    treat AFTER as a single-commit list."""
    if not before or before.strip("0") == "":
        out = _run(["git", "show", "--name-only", "--pretty=", after])
    else:
        out = _run(["git", "diff", "--name-only", before, after])
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def _gh_json(*args):
    out = _run(["gh"] + list(args))
    return json.loads(out) if out.strip() else None


def _list_status_issues(repo: str):
    return _gh_json(
        "issue", "list",
        "--repo", repo,
        "--state", "open",
        "--label", LABEL_STATUS,
        "--json", "number,body,assignees",
        "--limit", "100",
    ) or []


def _login_for(issue) -> str:
    assignees = issue.get("assignees") or []
    if not assignees:
        return ""
    return assignees[0].get("login") or ""


def _committed_on_primary_since(primary: str, path: str, since_iso: str) -> bool:
    """Return True iff `origin/<primary>` has any commit touching `path`
    with commit-date >= since_iso. Used to sweep declared entries whose
    merge to the default branch happened in this push OR in a previous
    push that a prior (failed) Action didn't clean up.

    Cached per (path, since_iso) because multiple sessions may declare
    the same path and we only need one git log call per combination.
    """
    key = (path, since_iso)
    if key in _PRIMARY_HIT_CACHE:
        return _PRIMARY_HIT_CACHE[key]
    try:
        out = _run([
            "git", "log",
            f"origin/{primary}",
            f"--since={since_iso}",
            "--format=%H",
            "-1",
            "--",
            path,
        ])
        result = bool(out.strip())
    except subprocess.CalledProcessError:
        # Fail closed — don't release on git errors.
        result = False
    _PRIMARY_HIT_CACHE[key] = result
    return result


def _ensure_remote_branch(branch: str) -> bool:
    """Make sure `origin/<branch>` is resolvable locally. Fetches if
    missing. Cached per branch. Returns True iff the ref is ready."""
    if branch in _BRANCH_READY_CACHE:
        return _BRANCH_READY_CACHE[branch]
    ready = False
    try:
        _run(["git", "rev-parse", "--verify", f"origin/{branch}"])
        ready = True
    except subprocess.CalledProcessError:
        try:
            _run(["git", "fetch", "--quiet", "origin", branch])
            _run(["git", "rev-parse", "--verify", f"origin/{branch}"])
            ready = True
        except subprocess.CalledProcessError:
            ready = False
    _BRANCH_READY_CACHE[branch] = ready
    return ready


def _path_synced_with_primary(primary: str, branch: str, path: str) -> bool:
    """Content-level comparison between `origin/<branch>` and
    `origin/<primary>` for a single path. True iff they are identical —
    which covers (a) merged content, (b) consistently-deleted paths,
    and (c) squash-merge net-zero cases where the primary never shows
    a commit touching the path."""
    key = (primary, branch, path)
    if key in _SYNCED_CACHE:
        return _SYNCED_CACHE[key]
    synced = False
    if _ensure_remote_branch(branch) and _ensure_remote_branch(primary):
        try:
            p = subprocess.run(
                ["git", "diff", "--quiet",
                 f"origin/{primary}", f"origin/{branch}", "--", path],
                capture_output=True, text=True,
            )
            synced = (p.returncode == 0)
        except subprocess.CalledProcessError:
            synced = False
    _SYNCED_CACHE[key] = synced
    return synced


def _should_release(primary: str, branch: str, path: str, declared_at: str) -> bool:
    """Decide whether a single declared path can be released.

    - Session working on primary directly → release when primary has any
      commit touching this path since declared_at (post-push, origin is
      at the tip).
    - Session working on a feature branch → release when the branch tip
      matches primary for this path (merged / net-zero / consistent-delete).
    - Branch unresolvable (deleted after merge?) → fall back to commit
      presence on primary since declared_at.
    """
    if not path:
        return False
    if branch and branch != primary:
        if _ensure_remote_branch(branch):
            return _path_synced_with_primary(primary, branch, path)
    # Either primary-branch session OR branch no longer fetchable.
    if declared_at:
        return _committed_on_primary_since(primary, path, declared_at)
    return False


def _release_in_session(session: state_mod.Session, primary: str) -> bool:
    """Drop declared_files entries whose state on `origin/<session.branch>`
    matches `origin/<primary>` (or, for primary-branch sessions, whose
    path has been committed on primary since declared_at)."""
    if not session.declared_files:
        return False
    kept = []
    for e in session.declared_files:
        if not isinstance(e, dict):
            kept.append(e)
            continue
        path = e.get("path")
        declared_at = (
            e.get("declared_at")
            or session.last_heartbeat
            or session.started_at
        )
        if path and _should_release(primary, session.branch, path, declared_at):
            continue
        kept.append(e)
    if len(kept) == len(session.declared_files):
        return False
    session.declared_files = kept
    return True


def _process_issue(repo: str, issue: dict, primary: str) -> str:
    """Returns one of: 'no-change', 'edited', 'closed'."""
    body = issue.get("body") or ""
    parsed, corrupt = state_mod.parse_body(body)
    if corrupt or not parsed.sessions:
        return "no-change"

    any_change = False
    surviving = []
    for s in parsed.sessions:
        released = _release_in_session(s, primary)
        any_change = any_change or released
        if s.declared_files:
            surviving.append(s)
        else:
            any_change = True  # session dropped
    if not any_change:
        return "no-change"
    parsed.sessions = surviving

    login = _login_for(issue) or "user"
    new_body = state_mod.render_body(parsed, login=login, preserve_tail_from=body)
    num = issue["number"]
    if not parsed.sessions:
        _run(["gh", "issue", "edit", str(num), "--repo", repo, "--body", new_body])
        try:
            _run(["gh", "issue", "edit", str(num), "--repo", repo,
                  "--remove-label", LABEL_ACTIVE], check=False)
        except subprocess.CalledProcessError:
            pass
        _run(["gh", "issue", "close", str(num), "--repo", repo])
        return "closed"
    _run(["gh", "issue", "edit", str(num), "--repo", repo, "--body", new_body])
    return "edited"


def main() -> int:
    repo = os.environ.get("REPO", "")
    after = os.environ.get("AFTER", "")
    primary = os.environ.get("PRIMARY", "").strip() or "main"
    if not repo or not after:
        print("[yoink-action] missing REPO / AFTER env", file=sys.stderr)
        return 2

    # v0.3.24: sweep every open yoink:status issue and release every
    # declared path that `origin/<primary>` already contains with a
    # commit newer than the entry's declared_at. This catches stale
    # entries left over from pushes where the Action failed or was
    # disabled — not only the paths in THIS push's diff.
    print(f"[yoink-action] sweeping against origin/{primary}.")

    try:
        issues = _list_status_issues(repo)
    except subprocess.CalledProcessError as e:
        print(f"[yoink-action] gh issue list failed: {e.stderr}", file=sys.stderr)
        return 2

    edited = closed = 0
    for issue in issues:
        try:
            result = _process_issue(repo, issue, primary)
        except subprocess.CalledProcessError as e:
            print(f"[yoink-action] issue #{issue.get('number')} failed: {e.stderr}",
                  file=sys.stderr)
            continue
        if result == "edited":
            edited += 1
            print(f"[yoink-action] issue #{issue['number']}: released paths.")
        elif result == "closed":
            closed += 1
            print(f"[yoink-action] issue #{issue['number']}: closed (no sessions left).")
    print(f"[yoink-action] done — {edited} edited, {closed} closed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
