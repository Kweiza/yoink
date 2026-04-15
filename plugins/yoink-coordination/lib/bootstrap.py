"""Bootstrap helpers for /yoink-coordination:bootstrap.

v0.3.21 split: the GitHub Actions release workflow YAML is treated as
user-owned (customizable `runs-on`, `permissions`, etc.) and bootstrap
only writes it on first install. The Python scripts under `.github/yoink/`
are plugin-owned and always updated on content diff.

If the template workflow YAML carries a newer schema version than the
user's file, bootstrap halts completely — no stage, no commit, no push —
and prints the diff for the user to merge manually. This prevents a
partial-update state where release.py v2 ships alongside workflow v1.
"""
from __future__ import annotations
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import constants
import gitops


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_ROOT = PLUGIN_ROOT / "templates" / "github"

WORKFLOW_REL = ".github/workflows/yoink-release.yml"
RELEASE_SCRIPT_REL = ".github/yoink/release.py"
STATE_SCRIPT_REL = ".github/yoink/state.py"

_SCHEMA_RE = re.compile(r"#\s*yoink-release workflow\s*[—-]\s*schema v(\d+)")


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


def _parse_schema(path: Path) -> Optional[int]:
    """Return the `schema v<N>` integer from the file header, or None."""
    if not path.exists():
        return None
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:1024]
    except OSError:
        return None
    m = _SCHEMA_RE.search(head)
    return int(m.group(1)) if m else None


def _print_schema_mismatch(dst: Path, tpl: Path,
                           user_schema: int, tpl_schema: int) -> None:
    print(
        f"[yoink] workflow schema mismatch: your {dst.name} is v{user_schema}, "
        f"template is v{tpl_schema}. Halting — NO files staged, committed, or pushed.",
        file=sys.stderr,
    )
    try:
        user_text = dst.read_text(encoding="utf-8").splitlines(keepends=True)
        tpl_text = tpl.read_text(encoding="utf-8").splitlines(keepends=True)
        diff = difflib.unified_diff(
            user_text, tpl_text,
            fromfile=f"yours (v{user_schema})",
            tofile=f"template (v{tpl_schema})",
        )
        sys.stderr.write("".join(diff))
    except OSError:
        pass
    print(
        "[yoink] Merge the template into your workflow (preserving your "
        "runs-on / permissions / etc.), then re-run "
        "`/yoink-coordination:bootstrap`.",
        file=sys.stderr,
    )


def _stage_yaml_if_fresh(cwd: Path) -> Optional[str]:
    """Copy the workflow template only on first install. Returns the
    staged relpath or None if skipped."""
    dst = cwd / WORKFLOW_REL
    if dst.exists():
        return None
    src = TEMPLATES_ROOT / "workflows/yoink-release.yml"
    content = src.read_text(encoding="utf-8")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content, encoding="utf-8")
    return WORKFLOW_REL


def _stage_script_if_changed(cwd: Path, src_rel: str, dst_rel: str) -> Optional[str]:
    """Copy script template if content differs. Always overwrites on diff."""
    src = TEMPLATES_ROOT / src_rel
    dst = cwd / dst_rel
    if not src.exists():
        print(f"[yoink] template missing: {src}", file=sys.stderr)
        return None
    new_content = src.read_text(encoding="utf-8")
    if dst.exists() and dst.read_text(encoding="utf-8") == new_content:
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(new_content, encoding="utf-8")
    return dst_rel


def install_release_workflow(cwd: Path) -> None:
    """Install / update the release workflow and scripts.

    Behaviour:
      - If the workflow YAML doesn't exist → copy it from the template.
      - If it exists and schema matches the template → leave it alone
        (preserves user's `runs-on`, `permissions`, etc.).
      - If it exists and schema differs → HALT (no stage, no commit, no
        push). Print diff and ask user to merge manually.
      - The `.github/yoink/release.py` and `.github/yoink/state.py`
        scripts are plugin-owned — overwritten on content diff.
      - When any file was staged, git add + commit + push. Push failure
        leaves the commit local with stderr guidance.
    """
    if not (cwd / ".git").exists():
        print(f"[yoink] {cwd}: not a git repo, skipping workflow install.",
              file=sys.stderr)
        return

    yaml_dst = cwd / WORKFLOW_REL
    yaml_tpl = TEMPLATES_ROOT / "workflows/yoink-release.yml"
    tpl_schema = _parse_schema(yaml_tpl)
    if tpl_schema is None:
        print("[yoink] template workflow has no schema marker; aborting install.",
              file=sys.stderr)
        return

    user_schema = _parse_schema(yaml_dst) if yaml_dst.exists() else None
    # Legacy grace: pre-v0.3.21 bootstrap wrote YAML without a marker.
    # Treat missing-but-existing as v1 so we don't flag it as a mismatch.
    if yaml_dst.exists() and user_schema is None:
        user_schema = 1

    if user_schema is not None and user_schema != tpl_schema:
        _print_schema_mismatch(yaml_dst, yaml_tpl, user_schema, tpl_schema)
        return  # halt everything

    changed = []
    staged_yaml = _stage_yaml_if_fresh(cwd)
    if staged_yaml:
        changed.append(staged_yaml)
    for src_rel, dst_rel in [
        ("yoink/release.py", RELEASE_SCRIPT_REL),
        ("yoink/state.py", STATE_SCRIPT_REL),
    ]:
        staged = _stage_script_if_changed(cwd, src_rel, dst_rel)
        if staged:
            changed.append(staged)

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
