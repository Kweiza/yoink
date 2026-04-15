"""Tests for lib/bootstrap.py (v0.3.7+)."""
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


def test_ensure_config_file_creates_with_main_fallback(tmp_path, capsys):
    """Non-remote repo → primary_branch falls back to 'main'."""
    repo = _init_repo(tmp_path / "r")
    bootstrap.ensure_config_file(repo)
    path = repo / ".claude" / "yoink.config.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data == {"primary_branch": "main"}
    assert "created" in capsys.readouterr().out


def test_ensure_config_file_respects_detected_primary(tmp_path, capsys):
    """Clone from an upstream with default branch 'trunk' → detected."""
    upstream = _init_repo(tmp_path / "up")
    (upstream / "seed.txt").write_text("seed")
    _git(upstream, "add", "seed.txt"); _git(upstream, "commit", "-qm", "init")
    _git(upstream, "branch", "-m", "trunk")

    repo = tmp_path / "r"
    subprocess.run(["git", "clone", "-q", str(upstream), str(repo)], check=True)
    _git(repo, "config", "user.email", "t@t"); _git(repo, "config", "user.name", "t")

    bootstrap.ensure_config_file(repo)
    data = json.loads((repo / ".claude" / "yoink.config.json").read_text())
    assert data == {"primary_branch": "trunk"}


def test_ensure_config_file_noop_when_already_exists(tmp_path, capsys):
    """Existing config file must NOT be overwritten."""
    repo = _init_repo(tmp_path / "r")
    (repo / ".claude").mkdir()
    original = json.dumps({"primary_branch": "develop", "conflict_mode": "block"})
    (repo / ".claude" / "yoink.config.json").write_text(original)

    bootstrap.ensure_config_file(repo)

    unchanged = (repo / ".claude" / "yoink.config.json").read_text()
    assert unchanged == original  # preserve user's existing settings verbatim
    assert "ok (exists, unchanged)" in capsys.readouterr().out


# ----- v0.3.19 install_release_workflow -----
def test_install_release_workflow_stages_files_and_commits(tmp_path, capsys):
    """Fresh repo + workflow not yet installed → 3 files staged, committed.
    Push fails (no remote) but commit must remain locally."""
    repo = _init_repo(tmp_path / "r")
    # need at least one initial commit for git push to even attempt
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "init")

    bootstrap.install_release_workflow(repo)
    out = capsys.readouterr()

    # Files exist
    assert (repo / ".github/workflows/yoink-release.yml").exists()
    assert (repo / ".github/yoink/release.py").exists()
    assert (repo / ".github/yoink/state.py").exists()

    # Commit happened
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%s", "-1"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert "yoink: install/update release workflow" in log

    # No upstream → push fails → message printed to stderr
    assert "push failed" in out.err or "push manually" in out.err


def test_install_release_workflow_idempotent(tmp_path, capsys):
    """Running twice with no template changes → second run is a no-op."""
    repo = _init_repo(tmp_path / "r")
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "init")

    bootstrap.install_release_workflow(repo)
    capsys.readouterr()  # drain
    bootstrap.install_release_workflow(repo)
    out = capsys.readouterr()
    assert "already up to date" in out.out
