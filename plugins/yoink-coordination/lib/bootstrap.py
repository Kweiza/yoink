"""Bootstrap helpers for /yoink-coordination:bootstrap (v0.3.7+).

v0.3.19 adds `install_release_workflow` which copies the GitHub Actions
release workflow templates into the user's repo, then commits and pushes
them so the Action can fire on subsequent merges to the default branch.

`ensure_config_file` is preserved as a no-op-on-existing helper for the
old primary_branch config. v0.3.20 will delete it once Actions release is
verified.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
from pathlib import Path

import constants
import gitops


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_ROOT = PLUGIN_ROOT / "templates" / "github"

# (template path relative to TEMPLATES_ROOT, target path relative to cwd)
_WORKFLOW_FILES = [
    ("workflows/yoink-release.yml", ".github/workflows/yoink-release.yml"),
    ("yoink/release.py", ".github/yoink/release.py"),
    ("yoink/state.py", ".github/yoink/state.py"),
]


def ensure_config_file(cwd: Path) -> None:
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


def _git(cwd: Path, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check, capture_output=True, text=True,
    )


def _stage_workflow_files(cwd: Path) -> list:
    """Copy templates into cwd. Returns list of dst paths that were created
    or updated. Files with identical content are skipped."""
    changed = []
    for src_rel, dst_rel in _WORKFLOW_FILES:
        src = TEMPLATES_ROOT / src_rel
        dst = cwd / dst_rel
        if not src.exists():
            print(f"[yoink] template missing: {src}", file=sys.stderr)
            continue
        new_content = src.read_text(encoding="utf-8")
        if dst.exists() and dst.read_text(encoding="utf-8") == new_content:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(new_content, encoding="utf-8")
        changed.append(dst_rel)
    return changed


def install_release_workflow(cwd: Path) -> None:
    """Copy + commit + push the release workflow files. Idempotent.

    Behavior:
      - If files already up to date → no commit, just inform.
      - Otherwise stage the three files, commit them with a fixed message,
        and push to the current branch's upstream. If push fails (no
        upstream / auth) → commit stays locally; user can push manually.
    """
    if not (cwd / ".git").exists():
        print(f"[yoink] {cwd}: not a git repo, skipping workflow install.",
              file=sys.stderr)
        return

    changed = _stage_workflow_files(cwd)
    if not changed:
        print("[yoink] release workflow already up to date.")
        return

    try:
        _git(cwd, "add", *changed)
    except subprocess.CalledProcessError as e:
        print(f"[yoink] git add failed: {e.stderr.strip()}", file=sys.stderr)
        return
    try:
        _git(cwd, "commit", "-m", "yoink: install/update release workflow")
    except subprocess.CalledProcessError as e:
        # e.g., pre-commit hook rejected
        print(f"[yoink] git commit failed: {e.stderr.strip()}", file=sys.stderr)
        return

    try:
        _git(cwd, "push")
        print(
            "[yoink] release workflow installed: "
            + ", ".join(changed)
            + " (committed + pushed)"
        )
    except subprocess.CalledProcessError as e:
        print(
            "[yoink] release workflow committed locally but push failed: "
            + e.stderr.strip()
            + "\n[yoink] push manually so GitHub Actions can run.",
            file=sys.stderr,
        )
