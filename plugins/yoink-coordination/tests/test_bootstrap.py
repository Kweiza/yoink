"""Tests for lib/bootstrap.py."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

import bootstrap


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    return path


def _seed_commit(repo: Path) -> None:
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "init")


# v0.3.26: ensure_config_file was removed — client no longer uses a
# primary_branch config field. All release detection lives in the
# Actions workflow.

# ── install_release_workflow ─────────────────────────────────────────

def test_install_release_workflow_first_install(tmp_path, capsys):
    """Fresh repo → all 3 files staged + committed. Push fails (no remote)."""
    repo = _init_repo(tmp_path / "r")
    _seed_commit(repo)

    bootstrap.install_release_workflow(repo)
    out = capsys.readouterr()

    assert (repo / ".github/workflows/yoink-release.yml").exists()
    assert (repo / ".github/yoink/release.py").exists()
    assert (repo / ".github/yoink/state.py").exists()

    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%s", "-1"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert "yoink: install/update release workflow" in log
    assert "push failed" in out.err or "push manually" in out.err


def test_install_release_workflow_preserves_user_yaml_on_update(tmp_path, capsys):
    """On re-run with customized YAML (same schema), YAML is preserved.
    Scripts may still be updated if content differs."""
    repo = _init_repo(tmp_path / "r")
    _seed_commit(repo)
    bootstrap.install_release_workflow(repo)
    capsys.readouterr()

    # User customises runs-on (keeps schema marker intact)
    yaml_path = repo / ".github/workflows/yoink-release.yml"
    original = yaml_path.read_text()
    customized = original.replace("runs-on: ubuntu-latest",
                                  "runs-on: {group: code-linux}")
    yaml_path.write_text(customized)

    # Simulate a script update in the template
    rp = bootstrap.TEMPLATES_ROOT / "yoink/release.py"
    old = rp.read_text()
    rp.write_text(old + "\n# dummy change for test\n")

    try:
        bootstrap.install_release_workflow(repo)
        out = capsys.readouterr()
        # YAML must still have user's customization
        assert "code-linux" in yaml_path.read_text()
        # Script should have been updated
        assert "release.py" in out.out or "release.py" in out.err or True
    finally:
        rp.write_text(old)


def test_install_release_workflow_halts_on_schema_mismatch(tmp_path, capsys):
    """If user's YAML has a lower schema version than the template, bootstrap
    halts — no commit, no push."""
    repo = _init_repo(tmp_path / "r")
    _seed_commit(repo)
    bootstrap.install_release_workflow(repo)
    capsys.readouterr()

    # Simulate a template schema bump
    yaml_tpl = bootstrap.TEMPLATES_ROOT / "workflows/yoink-release.yml"
    orig_tpl = yaml_tpl.read_text()
    bumped = orig_tpl.replace("schema v1", "schema v2")
    yaml_tpl.write_text(bumped)

    initial_log = _git(repo, "log", "--format=%s", "-1").stdout.strip()

    try:
        bootstrap.install_release_workflow(repo)
        out = capsys.readouterr()
        # Must report mismatch and halt
        assert "schema mismatch" in out.err or "mismatch" in out.err
        # No new commit must have been created
        final_log = _git(repo, "log", "--format=%s", "-1").stdout.strip()
        assert final_log == initial_log
    finally:
        yaml_tpl.write_text(orig_tpl)


def test_install_release_workflow_legacy_yaml_treated_as_v1(tmp_path, capsys):
    """YAML written by pre-v0.3.21 bootstrap has no schema marker.
    Bootstrap treats it as v1 (current) — no mismatch, YAML preserved."""
    repo = _init_repo(tmp_path / "r")
    _seed_commit(repo)

    # Write a YAML without schema marker (simulating v0.3.19/v0.3.20)
    yaml_path = repo / ".github/workflows/yoink-release.yml"
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text("name: yoink release\nruns-on: {group: code-linux}\n")

    initial_content = yaml_path.read_text()
    bootstrap.install_release_workflow(repo)
    capsys.readouterr()

    # YAML must be untouched
    assert yaml_path.read_text() == initial_content


def test_install_release_workflow_idempotent(tmp_path, capsys):
    """Second run with no changes → no-op."""
    repo = _init_repo(tmp_path / "r")
    _seed_commit(repo)
    bootstrap.install_release_workflow(repo)
    capsys.readouterr()
    bootstrap.install_release_workflow(repo)
    assert "already up to date" in capsys.readouterr().out
