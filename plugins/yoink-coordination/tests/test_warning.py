import warning

OWNER = {
    "login": "alice", "branch": "feature/login",
    "declared_at": "2026-04-14T10:44:17Z",
    "task_issue": "kweiza/yoink#42",
}


def test_format_advisory_warning_contains_path_owner_branch_and_mode():
    msg = warning.format_conflict(
        path="src/foo.py", owners=[OWNER], mode="advisory", now_iso="2026-04-14T10:47:29Z",
    )
    assert "src/foo.py" in msg
    assert "@alice" in msg
    assert "feature/login" in msg
    assert "mode: advisory" in msg
    assert "proceeding" in msg
    assert "kweiza/yoink#42" in msg


def test_format_block_warning_has_block_suggestion():
    msg = warning.format_conflict(
        path="src/foo.py", owners=[OWNER], mode="block", now_iso="2026-04-14T10:47:29Z",
    )
    assert "mode: block" in msg
    assert "conflict_mode=advisory" in msg  # override hint


def test_format_multi_owner_sorted_by_earliest_claim():
    younger = {"login": "bob", "branch": "b", "declared_at": "2026-04-14T10:46:00Z", "task_issue": None}
    older = {"login": "alice", "branch": "a", "declared_at": "2026-04-14T10:44:00Z", "task_issue": None}
    msg = warning.format_conflict(
        path="x.py", owners=[younger, older], mode="advisory", now_iso="2026-04-14T10:47:29Z",
    )
    # Oldest claim named first
    assert msg.index("@alice") < msg.index("@bob")


def test_relative_time_formatter():
    # 3m 12s ago
    s = warning.format_rel(earlier="2026-04-14T10:44:17Z", now="2026-04-14T10:47:29Z")
    assert s == "00:03:12"
