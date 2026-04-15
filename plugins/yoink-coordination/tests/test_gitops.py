import gitops
import pytest


@pytest.mark.parametrize("cmd,expected", [
    ("git commit -m 'hi'", True),
    ("git commit", True),
    ("git commit -am wip", True),
    ("git -C /tmp/x commit -m wip", True),
    ("git -c commit.gpgsign=false commit -m wip", True),
    ("git --git-dir=/tmp/.git commit -m wip", True),
    ("git --git-dir /tmp/.git commit -m wip", True),
    ("git --work-tree /tmp/x commit -m wip", True),
    ("cd src && git commit -m wip", True),
    ("git add . && git commit -m wip && git push", True),
    ("git status; git commit -m wip", True),
    ("git status\ngit commit -m wip", True),
    # heredoc-style commits (Claude Code's default pattern — must detect)
    (
        "git add x && git commit -m \"$(cat <<'EOF'\n"
        "feat: something\n\n"
        "Co-Authored-By: someone\n"
        "EOF\n"
        ")\" && git push",
        True,
    ),
    (
        "git commit -m \"$(cat <<'EOF'\nline1\nline2\nEOF\n)\"",
        True,
    ),
    # negatives
    ("git status", False),
    ("git commit-tree -m wip", False),
    ("git commit-graph write", False),
    ("echo 'git commit' >> notes.txt", False),
    # heredoc whose content contains "git commit" text — echo first, not git
    (
        "echo \"$(cat <<'EOF'\ngit commit -m fake\nEOF\n)\" > notes.txt",
        False,
    ),
    ("", False),
    ("ls", False),
    # alias / indirection (unsupported, must be False)
    ("gc -m wip", False),
    ("eval \"git commit -m wip\"", False),
])
def test_is_git_commit_command(cmd, expected):
    assert gitops.is_git_commit_command(cmd) is expected


import subprocess


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    return path


def test_working_tree_paths_lists_modified_and_untracked(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-qm", "i")
    (repo / "a.txt").write_text("a2")     # modified
    (repo / "b.txt").write_text("b")      # untracked
    paths = gitops.working_tree_paths(repo)
    assert "a.txt" in paths
    assert "b.txt" in paths


def test_working_tree_paths_clean_repo_empty(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-qm", "i")
    assert gitops.working_tree_paths(repo) == set()


def test_working_tree_paths_non_repo_returns_none(tmp_path):
    assert gitops.working_tree_paths(tmp_path / "nope") is None


def test_committed_paths_in_head_after_commit(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "x.txt").write_text("x")
    (repo / "sub").mkdir(); (repo / "sub" / "y.txt").write_text("y")
    _git(repo, "add", "x.txt", "sub/y.txt"); _git(repo, "commit", "-qm", "i")
    paths = gitops.committed_paths_in_head(repo)
    assert paths == {"x.txt", "sub/y.txt"}


def test_committed_paths_in_head_non_repo_returns_none(tmp_path):
    assert gitops.committed_paths_in_head(tmp_path / "nope") is None


def test_is_path_gitignored_true_for_ignored(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / ".gitignore").write_text("*.log\n")
    _git(repo, "add", ".gitignore"); _git(repo, "commit", "-qm", "ig")
    (repo / "noisy.log").write_text("x")
    assert gitops.is_path_gitignored(repo, "noisy.log") is True


def test_is_path_gitignored_false_for_tracked(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-qm", "i")
    assert gitops.is_path_gitignored(repo, "a.txt") is False


def test_is_path_gitignored_fails_open_on_non_repo(tmp_path):
    # Non-repo → treat as "not ignored" (fail-open); callers proceed normally.
    assert gitops.is_path_gitignored(tmp_path / "nope", "x.txt") is False


def test_working_tree_paths_includes_both_sides_of_rename(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "old.txt").write_text("content")
    _git(repo, "add", "old.txt"); _git(repo, "commit", "-qm", "i")
    _git(repo, "mv", "old.txt", "new.txt")
    # `git mv` stages the rename. porcelain will show `R  old.txt\0new.txt` (or similar).
    paths = gitops.working_tree_paths(repo)
    # Both the new name AND the old name must appear, per spec requirement that
    # rename old-name is parsed into the set.
    assert "new.txt" in paths
    assert "old.txt" in paths
