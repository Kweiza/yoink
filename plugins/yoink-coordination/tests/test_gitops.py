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


@pytest.mark.parametrize("cmd,expected", [
    # Positives: file restore commands
    ("git checkout -- file.txt", True),
    ("git checkout -- src/foo.py src/bar.py", True),
    ("git checkout file.txt", True),
    ("git restore file.txt", True),
    ("git restore --staged --worktree file.txt", True),
    ("git reset --hard", True),
    ("git reset --hard HEAD~1", True),
    ("git -C /tmp/repo checkout -- file.txt", True),
    ("cd src && git checkout -- file.txt", True),
    ("git restore file.txt && git status", True),
    # Broad trigger — accept branch switch (verification happens downstream)
    ("git checkout main", True),
    ("git switch main", True),
    # Negatives
    ("git stash", False),
    ("git stash pop", False),
    ("git revert abc123", False),
    ("git commit -m wip", False),
    ("git status", False),
    ("git add file.txt", False),
    ("git push", False),
    ("ls", False),
    ("", False),
    ("echo 'git checkout' >> notes.txt", False),
])
def test_is_git_revert_command(cmd, expected):
    assert gitops.is_git_revert_command(cmd) is expected


import subprocess


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    return path


def _init_repo_main(path):
    """Like _init_repo but forces the initial branch to `main`.

    git < 2.28 defaults to `master` without init.defaultBranch; passing
    --initial-branch keeps the new-test assertions portable without
    touching the shared _init_repo helper.
    """
    path.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["git", "-C", str(path), "init", "-q", "--initial-branch=main"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        # Older git (<2.28): init without the flag, then force-rename.
        _git(path, "init", "-q")
        _git(path, "checkout", "-q", "-B", "main")
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
    paths = gitops.working_tree_paths(repo)
    assert "new.txt" in paths
    assert "old.txt" in paths


# v0.3.26: detect_primary_branch and path_ahead_of_primary were removed
# — client-side release detection was fully replaced by the GitHub
# Actions release workflow (content-diff against origin/<primary>). The
# corresponding test cases were deleted with the code.


def test_branch_diff_paths_shows_committed_changes(tmp_path):
    repo = _init_repo_main(tmp_path / "r")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-b", "feature")
    (repo / "b.txt").write_text("b")
    _git(repo, "add", "b.txt"); _git(repo, "commit", "-qm", "add b")
    paths = gitops.branch_diff_paths(repo, "main")
    assert paths is not None
    assert "b.txt" in paths
    assert "a.txt" not in paths


def test_branch_diff_paths_empty_when_same(tmp_path):
    repo = _init_repo_main(tmp_path / "r")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-qm", "init")
    paths = gitops.branch_diff_paths(repo, "main")
    assert paths == set()


def test_branch_diff_paths_non_repo_returns_none(tmp_path):
    assert gitops.branch_diff_paths(tmp_path / "nope", "main") is None


def test_remote_branch_exists_false_for_nonexistent(tmp_path):
    repo = _init_repo(tmp_path / "r")
    # No remote configured
    assert gitops.remote_branch_exists(repo, "nonexistent") is False


def test_remote_branch_exists_non_repo_returns_false(tmp_path):
    assert gitops.remote_branch_exists(tmp_path / "nope", "main") is False


def test_git_repo_healthy_true_in_repo(tmp_path):
    repo = _init_repo_main(tmp_path / "r")
    assert gitops.git_repo_healthy(repo) is True


def test_git_repo_healthy_false_for_non_repo(tmp_path):
    assert gitops.git_repo_healthy(tmp_path / "nope") is False


def test_is_file_still_claimed_uncommitted_modification(tmp_path):
    repo = _init_repo_main(tmp_path / "r")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-qm", "init")
    (repo / "a.txt").write_text("modified")
    assert gitops.is_file_still_claimed(repo, "a.txt", "main") is True


def test_is_file_still_claimed_committed_on_branch(tmp_path):
    repo = _init_repo_main(tmp_path / "r")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-qm", "init")
    _git(repo, "checkout", "-b", "feature")
    (repo / "b.txt").write_text("b")
    _git(repo, "add", "b.txt"); _git(repo, "commit", "-qm", "add b")
    assert gitops.is_file_still_claimed(repo, "b.txt", "main") is True


def test_is_file_still_claimed_reverted_file(tmp_path):
    repo = _init_repo_main(tmp_path / "r")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-qm", "init")
    (repo / "a.txt").write_text("modified")
    _git(repo, "checkout", "--", "a.txt")
    assert gitops.is_file_still_claimed(repo, "a.txt", "main") is False


def test_is_file_still_claimed_non_repo_returns_true(tmp_path):
    # fail-open: can't determine → assume still claimed
    assert gitops.is_file_still_claimed(tmp_path / "nope", "x.txt", "main") is True


def test_is_file_still_claimed_clean_file_on_main_not_claimed(tmp_path):
    """File exists in repo but hasn't been modified and we're on main
    (no branch diff). Should NOT be claimed."""
    repo = _init_repo_main(tmp_path / "r")
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt"); _git(repo, "commit", "-qm", "init")
    # Clean repo on main — nothing to claim
    assert gitops.is_file_still_claimed(repo, "a.txt", "main") is False
